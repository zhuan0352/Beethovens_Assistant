"""Turn a frame-level pitch track into discrete notes.

A hummed melody is messy: the singer isn't tuned to A440, notes glide into each
other, and repeated notes are separated only by a dip in loudness. The steps:

1. voicing gate (periodicity + adaptive loudness threshold)
2. median smoothing and short-gap filling
3. split on sustained pitch change, silence, or a loudness dip
4. absorb glide fragments, estimate the singer's tuning offset, round to semitones
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np

from .pitch import PitchTrack

MIN_NOTE_S = 0.08
MAX_GLIDE_S = 0.16  # a span this short with no steady pitch is a scoop or slide, not a note
STEADY_SLOPE = 0.25  # semitones per frame; faster than this and the voice is still travelling
MIN_STEADY_FRAMES = 4
GLIDE_TRAVEL = 0.6  # semitones covered by a short span before it counts as a slide
PITCH_SPLIT = 0.8  # semitones away from the current note before we consider a new one
DIP_DB = 6.0


@dataclass
class RawNote:
    start: float  # seconds
    end: float
    pitch: int  # MIDI, after tuning correction
    velocity: int
    cents: float  # how far the sung pitch was from the rounded one


def _nanmedian_filter(v: np.ndarray, width: int) -> np.ndarray:
    half = width // 2
    padded = np.pad(v, (half, half), constant_values=np.nan)
    windows = np.lib.stride_tricks.sliding_window_view(padded, width)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        med = np.nanmedian(windows, axis=1)
    return np.where(np.isnan(v), np.nan, med)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index pairs of consecutive True values."""
    if not mask.any():
        return []
    edges = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def _fill_gaps(m: np.ndarray, max_gap: int) -> np.ndarray:
    out = m.copy()
    for s, e in _runs(np.isnan(m)):
        if s == 0 or e == len(m) or e - s > max_gap:
            continue
        left, right = m[s - 1], m[e]
        if abs(left - right) <= 1.5:
            out[s:e] = np.linspace(left, right, e - s + 2)[1:-1]
    return out


def _split_on_pitch(p: np.ndarray) -> list[int]:
    """Boundaries (relative indices) where the pitch settles somewhere new."""
    bounds = [0]
    start = 0
    n = len(p)
    i = 1
    while i < n:
        center = np.median(p[start:i])
        if abs(p[i] - center) > PITCH_SPLIT:
            j = min(n, i + 4)
            ahead = p[i:j] - center
            if j - i >= 3 and np.all(np.abs(ahead) > 0.6) and np.all(np.sign(ahead) == np.sign(ahead[0])):
                bounds.append(i)
                start = i
        i += 1
    return bounds


def _split_on_dips(db: np.ndarray, s: int, e: int) -> list[int]:
    """Re-articulations of the same pitch show up as a dip in loudness."""
    cuts = []
    last = s
    i = s + 5
    while i < e - 5:
        left_peak = db[last:i].max()
        right_peak = db[i : min(e, i + 15)].max()
        is_min = db[i] <= db[i - 1] and db[i] <= db[i + 1]
        if is_min and db[i] <= left_peak - DIP_DB and db[i] <= right_peak - DIP_DB:
            cuts.append(i)
            last = i
            i += 5
        else:
            i += 1
    return cuts


def segment_notes(track: PitchTrack) -> tuple[list[RawNote], float, dict]:
    """Returns (notes, tuning offset in cents, contour for display)."""
    hop = track.hop_s
    f0 = np.clip(track.f0, 1.0, None)
    midi = 69.0 + 12.0 * np.log2(f0 / 440.0)

    p95 = float(np.percentile(track.rms, 95))
    p05 = float(np.percentile(track.rms, 5))
    gate = max(0.06 * p95, min(2.0 * p05, 0.3 * p95), 1e-4)
    voiced = (track.aperiodicity < 0.3) & (track.rms > gate) & (midi > 34) & (midi < 86)

    m = np.where(voiced, midi, np.nan)
    m = _nanmedian_filter(m, 5)
    m = _fill_gaps(m, max_gap=3)
    for s, e in _runs(~np.isnan(m)):
        if e - s < 4:
            m[s:e] = np.nan

    db = 20.0 * np.log10(np.maximum(track.rms, 1e-6))
    db = np.convolve(db, np.ones(3) / 3.0, mode="same")

    # --- segmentation: (start, end, reason-this-note-started)
    spans: list[list] = []
    for s, e in _runs(~np.isnan(m)):
        bounds = _split_on_pitch(m[s:e])
        edges = [s + b for b in bounds] + [e]
        for k in range(len(edges) - 1):
            a, b = edges[k], edges[k + 1]
            reason = "gap" if k == 0 else "pitch"
            cuts = _split_on_dips(db, a, b)
            pieces = [a] + cuts + [b]
            for q in range(len(pieces) - 1):
                spans.append([pieces[q], pieces[q + 1], reason if q == 0 else "dip"])

    # --- which frames hold a steady pitch (as opposed to gliding between notes)
    step_fwd = np.abs(np.diff(m, append=np.nan))
    step_bwd = np.abs(np.diff(m, prepend=np.nan))
    slope = np.fmax(np.nan_to_num(step_fwd), np.nan_to_num(step_bwd))
    steady = ~np.isnan(m) & (slope < STEADY_SLOPE)

    # --- absorb fragments: too short, or never settling on a pitch (scoops and
    # slides). They become the onset of the note they lead into.
    min_frames = int(round(MIN_NOTE_S / hop))
    max_glide = int(round(MAX_GLIDE_S / hop))

    def travel(a: int, b: int) -> float:
        """How far the pitch moved between the start and end of a span."""
        return abs(float(np.nanmean(m[b - 2 : b]) - np.nanmean(m[a : a + 2])))

    def is_fragment(a: int, b: int) -> bool:
        if b - a < min_frames:
            return True
        if b - a > max_glide:
            return False
        return steady[a:b].sum() < MIN_STEADY_FRAMES or travel(a, b) >= GLIDE_TRAVEL

    changed = True
    while changed:
        changed = False
        for i, (a, b, _reason) in enumerate(spans):
            if not is_fragment(a, b):
                continue
            prev_adjacent = i > 0 and a - spans[i - 1][1] <= 2
            next_adjacent = i + 1 < len(spans) and spans[i + 1][0] - b <= 2
            if next_adjacent and spans[i + 1][2] == "pitch":
                spans[i + 1][0] = a
                spans[i + 1][2] = spans[i][2]
            elif prev_adjacent:
                spans[i - 1][1] = b
            elif next_adjacent:
                spans[i + 1][0] = a
            spans.pop(i)
            changed = True
            break

    if not spans:
        return [], 0.0, {"t": [], "midi": []}

    def span_pitch(a: int, b: int) -> float:
        # judge the note by where the voice held still, not by the slide into it
        held = m[a:b][steady[a:b]]
        if len(held) >= MIN_STEADY_FRAMES:
            if b - a <= max_glide:  # short notes are mostly slide: trust where they end up
                held = held[len(held) // 2 :]
            return float(np.median(held))
        # nanmedian: a span that absorbed a neighbour can bridge a short unvoiced gap
        return float(np.nanmedian(m[a:b]))

    # --- tuning: where does this singer's "semitone grid" sit relative to A440?
    centers = np.array([span_pitch(a, b) for a, b, _ in spans])
    weights = np.array([b - a for a, b, _ in spans], dtype=float)
    ang = 2.0 * np.pi * (centers - np.round(centers))
    offset = float(np.arctan2((weights * np.sin(ang)).sum(), (weights * np.cos(ang)).sum()) / (2.0 * np.pi))

    # --- merge pitch-splits that round to the same semitone (vibrato, drift)
    merged: list[list] = []
    for a, b, reason in spans:
        pitch = int(round(span_pitch(a, b) - offset))
        if merged and reason == "pitch" and merged[-1][3] == pitch and a - merged[-1][1] <= 2:
            merged[-1][1] = b
        else:
            merged.append([a, b, reason, pitch])

    peak_db = max(float(db[a:b].mean()) for a, b, _, _ in merged)
    notes = []
    for a, b, _reason, _ in merged:
        center = span_pitch(a, b) - offset
        pitch = int(round(center))
        loud = float(db[a:b].mean()) - peak_db  # <= 0
        velocity = int(np.clip(100 + 2.0 * loud, 60, 110))
        notes.append(
            RawNote(
                start=float(track.times[a]),
                end=float(track.times[b - 1] + hop),
                pitch=pitch,
                velocity=velocity,
                cents=round((center - pitch) * 100.0, 1),
            )
        )

    step = 2
    shown = m[::step] - offset
    contour = {
        "t": [round(float(t), 3) for t in track.times[::step]],
        "midi": [None if np.isnan(v) else round(float(v), 2) for v in shown],
    }
    return notes, round(offset * 100.0, 1), contour
