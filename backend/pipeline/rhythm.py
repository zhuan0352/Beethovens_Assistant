"""Tempo estimation and quantisation of sung notes onto a 16th-note grid."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .notes import RawNote

BPM_MIN, BPM_MAX = 60.0, 180.0
DEFAULT_BPM = 100.0


@dataclass
class MelodyNote:
    pitch: int
    start: float  # beats (quarter notes) from the start of bar 1
    dur: float
    vel: int

    @property
    def end(self) -> float:
        return self.start + self.dur


def _concentration(t: np.ndarray, w: np.ndarray, period: np.ndarray) -> np.ndarray:
    """How tightly onsets cluster on a grid of the given period (0..1), any phase."""
    phase = 2.0 * np.pi * t[None, :] / period[:, None]
    return np.abs((w[None, :] * np.exp(1j * phase)).sum(axis=1)) / w.sum()


def estimate_tempo(notes: list[RawNote]) -> tuple[float, list[dict]]:
    """Pick the tempo whose beat / eighth / sixteenth grids best explain the
    onsets, with a mild preference for moderate tempos to settle the octave
    ambiguity (60 vs 120 fit equally well)."""
    if len(notes) < 4:
        return DEFAULT_BPM, []
    t = np.array([n.start for n in notes])
    t = t - t[0]
    w = np.array([min(n.end - n.start, 1.0) + 0.2 for n in notes])
    bpms = np.arange(BPM_MIN, BPM_MAX + 0.01, 0.5)
    beat = 60.0 / bpms
    score = (
        0.45 * _concentration(t, w, beat)
        + 0.40 * _concentration(t, w, beat / 2.0)
        + 0.15 * _concentration(t, w, beat / 4.0)
        - 0.12 * np.abs(np.log2(bpms / 108.0))
    )
    order = np.argsort(score)[::-1]
    best = float(bpms[order[0]])
    # report a few distinct alternatives for the UI
    alts: list[dict] = []
    for i in order:
        b = float(bpms[i])
        if all(abs(b - a["bpm"]) > 8 for a in alts):
            alts.append({"bpm": b, "score": round(float(score[i]), 3)})
        if len(alts) == 3:
            break
    return best, alts


def _fit_grid(t: np.ndarray, w: np.ndarray, bpm: float, lock_tempo: bool) -> tuple[float, float]:
    """Least-squares fit of onset times to a + step * k. Returns (phase, 16th duration)."""
    step = 15.0 / bpm
    eighth = 2.0 * step
    z = (w * np.exp(2j * np.pi * t / eighth)).sum()
    a = float(np.angle(z) / (2.0 * np.pi) * eighth)
    for _ in range(3):
        k = np.round((t - a) / step)
        if not lock_tempo and len(np.unique(k)) >= 3:
            kw = (w * k).sum() / w.sum()
            tw = (w * t).sum() / w.sum()
            var = (w * (k - kw) ** 2).sum()
            new_step = float((w * (k - kw) * (t - tw)).sum() / var) if var > 0 else step
            if abs(new_step / (15.0 / bpm) - 1.0) < 0.06:
                step = new_step
            a = float(tw - step * kw)
        else:
            a = float((w * (t - step * k)).sum() / w.sum())
    return a, step


def quantize(
    notes: list[RawNote], bpm: float, lock_tempo: bool = False
) -> tuple[list[MelodyNote], float]:
    """Snap notes to the grid. Returns the melody (in beats) and the refined bpm."""
    if not notes:
        return [], bpm
    t = np.array([n.start for n in notes])
    w = np.array([min(n.end - n.start, 1.0) + 0.2 for n in notes])
    a, step = _fit_grid(t, w, bpm, lock_tempo)
    bpm = 15.0 / step

    def snap(pos: float) -> int:
        # eighth-note positions unless the onset is clearly on an odd sixteenth
        eighth = 2 * round(pos / 2.0)
        return int(eighth) if abs(pos - eighth) < 0.7 else int(round(pos))

    starts = [snap((n.start - a) / step) for n in notes]
    ends = [int(round((n.end - a) / step)) for n in notes]

    for i in range(1, len(starts)):
        if starts[i] <= starts[i - 1]:
            starts[i] = starts[i - 1] + 1
    durs = []
    for i in range(len(starts)):
        d = max(1, ends[i] - starts[i])
        if i + 1 < len(starts):
            gap = starts[i + 1] - (starts[i] + d)
            if gap < 0 or gap == 1:  # trim overlaps; close breath-sized gaps
                d = starts[i + 1] - starts[i]
        durs.append(d)

    # choose which sixteenth is "the beat": long notes tend to start on beats
    def beat_score(o: int) -> float:
        s = 0.0
        for st, d in zip(starts, durs):
            if (st - o) % 4 == 0:
                s += d
            elif (st - o) % 2 == 0:
                s += 0.5 * d
        if (starts[0] - o) % 4 == 0:
            s += 0.5
        return s

    offset = max(range(4), key=beat_score)
    shift = starts[0] - ((starts[0] - offset) % 4)

    melody = [
        MelodyNote(pitch=n.pitch, start=(st - shift) / 4.0, dur=d / 4.0, vel=n.velocity)
        for n, st, d in zip(notes, starts, durs)
    ]
    return melody, round(bpm, 1)
