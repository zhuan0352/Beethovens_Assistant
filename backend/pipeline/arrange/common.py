"""Shared building blocks for the arrangement styles."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..harmony import Chord, Key
from ..rhythm import MelodyNote

DRUM_KICK, DRUM_RIM, DRUM_SNARE, DRUM_CLAP = 36, 37, 38, 39
DRUM_HAT, DRUM_OPEN_HAT, DRUM_SHAKER = 42, 46, 70


@dataclass
class Track:
    id: str
    name: str
    instrument: str  # voice name understood by the browser audio engine
    program: int  # General MIDI program for the .mid export
    gain: float = 0.8
    pan: float = 0.0
    reverb: float = 0.2
    role: str = "accompaniment"  # "melody" | "accompaniment" | "bass" | "drums"
    fx: dict = field(default_factory=dict)
    notes: list[list[float]] = field(default_factory=list)

    def add(self, pitch: int, start: float, dur: float, vel: float) -> None:
        if dur <= 0 or start < 0:
            return
        self.notes.append([int(pitch), float(start), float(dur), int(max(1, min(127, round(vel))))])

    def to_dict(self) -> dict:
        notes = sorted(self.notes, key=lambda n: (n[1], n[0]))
        return {
            "id": self.id, "name": self.name, "instrument": self.instrument, "program": self.program,
            "gain": self.gain, "pan": self.pan, "reverb": self.reverb, "role": self.role, "fx": self.fx,
            "notes": [[n[0], round(n[1], 4), round(n[2], 4), n[3]] for n in notes],
        }


@dataclass
class Context:
    melody: list[MelodyNote]
    chords: list[Chord]
    key: Key
    bpm: float
    beats_per_bar: int
    seed: int = 0

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)

    @property
    def total_beats(self) -> float:
        return self.chords[-1].end

    @property
    def bars(self) -> int:
        return int(round(self.total_beats / self.beats_per_bar))

    @property
    def body(self) -> list[Chord]:
        """All chords except the ending bar."""
        return self.chords[:-1]

    @property
    def ending(self) -> Chord:
        return self.chords[-1]

    def chord_at(self, beat: float) -> Chord:
        for c in self.chords:
            if c.start - 1e-6 <= beat < c.end - 1e-6:
                return c
        return self.chords[-1]

    def seconds(self, s: float) -> float:
        """Convert a duration in seconds to beats."""
        return s * self.bpm / 60.0

    def intensity(self, beat: float) -> float:
        """0..1 ramp across the piece; styles use it to build towards the end."""
        body_end = self.ending.start
        return min(1.0, max(0.0, beat / body_end)) if body_end > 0 else 1.0


def fit_register(melody: list[MelodyNote], center: int, lo: int, hi: int) -> list[MelodyNote]:
    """Transpose by whole octaves so the tune sits in an instrument's sweet spot.
    People hum wherever is comfortable; the lead instrument shouldn't."""
    if not melody:
        return []
    pitches = sorted(n.pitch for n in melody)
    median = pitches[len(pitches) // 2]
    shift = 12 * round((center - median) / 12)
    while pitches[-1] + shift > hi and pitches[0] + shift - 12 >= lo:
        shift -= 12
    while pitches[0] + shift < lo:
        shift += 12
    return [MelodyNote(n.pitch + shift, n.start, n.dur, n.vel) for n in melody]


def legato(melody: list[MelodyNote], max_gap: float = 0.5, overlap: float = 0.0) -> list[MelodyNote]:
    """Stretch notes across small gaps, as a bowed or blown instrument would."""
    out = []
    for i, n in enumerate(melody):
        dur = n.dur
        if i + 1 < len(melody):
            gap = melody[i + 1].start - n.end
            if 0 <= gap <= max_gap:
                dur = melody[i + 1].start - n.start + overlap
        out.append(MelodyNote(n.pitch, n.start, dur, n.vel))
    return out


def bass_pitch(root_pc: int, lo: int) -> int:
    """Lowest pitch with this pitch class at or above `lo`."""
    return lo + (root_pc - lo) % 12


def voice_chord(chord: Chord, lo: int, hi: int, prev: list[int] | None = None, top: int | None = None) -> list[int]:
    """Close-position triad within [lo, hi]. Picks the inversion that moves
    least from the previous voicing; `top` keeps it under the melody."""
    if top is not None:
        hi = max(lo + 9, min(hi, top))
    options = []
    for inversion in range(3):
        ivs = chord.intervals[inversion:] + tuple(i + 12 for i in chord.intervals[:inversion])
        base = bass_pitch((chord.root + ivs[0]) % 12, lo)
        while True:
            notes = [base + (iv - ivs[0]) for iv in ivs]
            if notes[-1] > hi:
                break
            options.append(notes)
            base += 12
    if not options:
        base = bass_pitch(chord.root, lo)
        return [base + iv for iv in chord.intervals]
    if prev:
        return min(options, key=lambda v: sum(abs(a - b) for a, b in zip(v, prev)))
    mid = (lo + hi) / 2.0
    return min(options, key=lambda v: abs(sum(v) / 3.0 - mid))


def lowest_melody_pitch(melody: list[MelodyNote], start: float, end: float) -> int | None:
    pitches = [n.pitch for n in melody if n.start < end and n.end > start]
    return min(pitches) if pitches else None


def humanize(track: Track, rng: random.Random, timing: float = 0.008, velocity: int = 5) -> None:
    """Small deterministic timing/velocity variations so parts don't sound stamped."""
    for n in track.notes:
        n[1] = max(0.0, n[1] + rng.uniform(-timing, timing))
        n[3] = int(max(1, min(127, n[3] + rng.randint(-velocity, velocity))))
