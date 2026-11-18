"""Melody-to-arrangement pipeline.

    audio ──► pitch track ──► notes (seconds) ──► tempo + quantise ──► key + chords ──► arrangements

`transcribe` does the expensive audio analysis once. `arrange_project` is cheap
and can be re-run with a different tempo, key or time signature."""

from __future__ import annotations

from dataclasses import asdict

from .arrange import build_all
from .audio import ANALYSIS_SR, resample
from .harmony import Key, detect_key, harmonize
from .notes import RawNote, segment_notes
from .pitch import track_pitch
from .rhythm import BPM_MAX, BPM_MIN, estimate_tempo, quantize


class NoMelodyError(ValueError):
    pass


def transcribe(samples, sr: int) -> dict:
    """Audio -> raw notes in seconds, plus what the UI needs to draw the take."""
    x = resample(samples, sr, ANALYSIS_SR)
    track = track_pitch(x, ANALYSIS_SR)
    notes, tuning_cents, contour = segment_notes(track)
    if len(notes) < 2:
        raise NoMelodyError(
            "Couldn't find a melody in that recording. Try humming a little louder, "
            "closer to the microphone, with clear separate notes."
        )
    return {
        "duration": round(len(x) / ANALYSIS_SR, 3),
        "tuning_cents": tuning_cents,
        "contour": contour,
        "raw_notes": [asdict(n) for n in notes],
    }


def arrange_project(
    transcription: dict,
    bpm: float | None = None,
    key: dict | None = None,
    beats_per_bar: int = 4,
) -> dict:
    """Raw notes -> quantised melody, chords and every arrangement style."""
    raw = [RawNote(**n) for n in transcription["raw_notes"]]
    detected_bpm, alternatives = estimate_tempo(raw)
    if bpm is None:
        melody, final_bpm = quantize(raw, detected_bpm)
    else:
        melody, final_bpm = quantize(raw, min(max(float(bpm), 40.0), 240.0), lock_tempo=True)

    detected_key = detect_key(melody)
    final_key = Key(int(key["tonic"]) % 12, key["mode"]) if key else detected_key
    chords = harmonize(melody, final_key, beats_per_bar)
    arrangements = build_all(melody, chords, final_key, final_bpm, beats_per_bar)

    return {
        "bpm": final_bpm,
        "detected_bpm": detected_bpm,
        "bpm_alternatives": alternatives,
        "bpm_range": [BPM_MIN, BPM_MAX],
        "key": final_key.to_dict(),
        "detected_key": detected_key.to_dict(),
        "beats_per_bar": beats_per_bar,
        "total_beats": chords[-1].end,
        "melody": [[n.pitch, n.start, n.dur, n.vel] for n in melody],
        "chords": [c.to_dict() for c in chords],
        "arrangements": arrangements,
    }
