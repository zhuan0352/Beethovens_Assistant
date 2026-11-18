"""Key detection and chord selection for a monophonic melody."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .rhythm import MelodyNote

# Krumhansl-Kessler key profiles
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
FLAT_MAJOR_KEYS = {5, 10, 3, 8, 1}  # F Bb Eb Ab Db
FLAT_MINOR_KEYS = {2, 7, 0, 5, 10, 3}  # Dm Gm Cm Fm Bbm Ebm

QUALITY_INTERVALS = {"maj": (0, 4, 7), "min": (0, 3, 7), "dim": (0, 3, 6)}
QUALITY_SUFFIX = {"maj": "", "min": "m", "dim": "dim"}

# (scale-degree offset in semitones, quality, roman numeral, prior)
MAJOR_CHORDS = [
    (0, "maj", "I", 0.30),
    (2, "min", "ii", 0.05),
    (4, "min", "iii", 0.0),
    (5, "maj", "IV", 0.15),
    (7, "maj", "V", 0.15),
    (9, "min", "vi", 0.10),
]
MINOR_CHORDS = [
    (0, "min", "i", 0.30),
    (3, "maj", "III", 0.05),
    (5, "min", "iv", 0.15),
    (7, "min", "v", 0.0),
    (7, "maj", "V", 0.10),
    (8, "maj", "VI", 0.10),
    (10, "maj", "VII", 0.05),
]

# how naturally one chord leads to the next (unlisted pairs score 0)
MAJOR_MOVES = {
    ("I", "IV"): 0.3, ("I", "V"): 0.3, ("I", "vi"): 0.3, ("I", "ii"): 0.2, ("I", "iii"): 0.1,
    ("ii", "V"): 0.5, ("ii", "IV"): 0.1,
    ("iii", "vi"): 0.3, ("iii", "IV"): 0.3,
    ("IV", "V"): 0.4, ("IV", "I"): 0.35, ("IV", "ii"): 0.1, ("IV", "vi"): 0.1,
    ("V", "I"): 0.5, ("V", "vi"): 0.3, ("V", "IV"): -0.1,
    ("vi", "IV"): 0.4, ("vi", "ii"): 0.3, ("vi", "V"): 0.2, ("vi", "iii"): 0.1,
}
MINOR_MOVES = {
    ("i", "iv"): 0.3, ("i", "VI"): 0.3, ("i", "VII"): 0.3, ("i", "III"): 0.2, ("i", "V"): 0.3, ("i", "v"): 0.1,
    ("III", "VI"): 0.3, ("III", "VII"): 0.3, ("III", "iv"): 0.2,
    ("iv", "V"): 0.4, ("iv", "i"): 0.3, ("iv", "VII"): 0.3, ("iv", "v"): 0.2,
    ("v", "i"): 0.3, ("v", "VI"): 0.2,
    ("V", "i"): 0.5, ("V", "VI"): 0.3,
    ("VI", "VII"): 0.4, ("VI", "III"): 0.3, ("VI", "iv"): 0.2, ("VI", "V"): 0.3,
    ("VII", "i"): 0.4, ("VII", "III"): 0.4, ("VII", "VI"): 0.1,
}


@dataclass
class Key:
    tonic: int  # pitch class 0..11
    mode: str  # "major" | "minor"
    confidence: float = 0.0

    @property
    def uses_flats(self) -> bool:
        return self.tonic in (FLAT_MAJOR_KEYS if self.mode == "major" else FLAT_MINOR_KEYS)

    def note_name(self, pc: int) -> str:
        return (FLAT_NAMES if self.uses_flats else SHARP_NAMES)[pc % 12]

    @property
    def name(self) -> str:
        return f"{self.note_name(self.tonic)} {self.mode}"

    def to_dict(self) -> dict:
        return {"tonic": self.tonic, "mode": self.mode, "name": self.name, "confidence": self.confidence}


@dataclass
class Chord:
    start: float  # beats
    dur: float
    root: int  # pitch class
    quality: str
    name: str
    degree: str

    @property
    def end(self) -> float:
        return self.start + self.dur

    @property
    def intervals(self) -> tuple[int, int, int]:
        return QUALITY_INTERVALS[self.quality]

    @property
    def pcs(self) -> list[int]:
        return [(self.root + i) % 12 for i in self.intervals]

    def to_dict(self) -> dict:
        return {
            "start": self.start, "dur": self.dur, "root": self.root,
            "quality": self.quality, "name": self.name, "degree": self.degree,
        }


def detect_key(melody: list[MelodyNote]) -> Key:
    hist = np.zeros(12)
    for n in melody:
        hist[n.pitch % 12] += n.dur
    if hist.sum() == 0:
        return Key(0, "major")
    last_pc = melody[-1].pitch % 12
    first_pc = melody[0].pitch % 12

    scores = []
    for mode, profile in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
        third = 4 if mode == "major" else 3
        for tonic in range(12):
            r = float(np.corrcoef(hist, np.roll(profile, tonic))[0, 1])
            # melodies overwhelmingly end on the tonic, and often start in its triad
            if last_pc == tonic:
                r += 0.12
            elif (last_pc - tonic) % 12 in (third, 7):
                r += 0.03
            if (first_pc - tonic) % 12 in (0, third, 7):
                r += 0.03
            scores.append((r, tonic, mode))
    scores.sort(reverse=True)
    best = scores[0]
    return Key(best[1], best[2], round(best[0] - scores[1][0], 3))


def _fit(chord_root: int, quality: str, degree: str, pc: int) -> float:
    """How well a melody pitch class sits on a chord."""
    iv = (pc - chord_root) % 12
    tones = QUALITY_INTERVALS[quality]
    if iv in tones:
        return 1.0
    if degree == "V" and iv == 10:
        return 0.6  # dominant seventh
    if any((iv - t) % 12 == 1 for t in tones):
        return -1.0  # a semitone above a chord tone clashes
    if iv in (2, 9):
        return -0.2  # ninth / sixth: colour, not a clash
    return -0.5


def harmonize(melody: list[MelodyNote], key: Key, beats_per_bar: int) -> list[Chord]:
    """Viterbi search over diatonic chords. In 4/4 the search runs on half-bars
    but changing chord mid-bar is penalised, so most bars get a single chord.
    One extra bar on the tonic is appended as an ending."""
    table = MAJOR_CHORDS if key.mode == "major" else MINOR_CHORDS
    moves = MAJOR_MOVES if key.mode == "major" else MINOR_MOVES
    tonic_degree = table[0][2]

    seg = beats_per_bar / 2.0 if beats_per_bar == 4 else float(beats_per_bar)
    per_bar = int(round(beats_per_bar / seg))
    melody_end = max((n.end for n in melody), default=0.0)
    n_bars = max(1, int(np.ceil(melody_end / beats_per_bar - 1e-6)))
    n_seg = n_bars * per_bar
    n_ch = len(table)

    emit = np.zeros((n_seg, n_ch))
    for s in range(n_seg):
        a, b = s * seg, (s + 1) * seg
        for c, (off, quality, degree, prior) in enumerate(table):
            root = (key.tonic + off) % 12
            total = 0.0
            for n in melody:
                ov = min(n.end, b) - max(n.start, a)
                if ov <= 0:
                    continue
                w = ov * (1.5 if abs(n.start - a) < 1e-6 else 1.0)
                total += w * _fit(root, quality, degree, n.pitch % 12)
            emit[s, c] = total / seg + prior
    emit[0, 0] += 0.4
    if melody and (melody[-1].pitch - key.tonic) % 12 in QUALITY_INTERVALS[table[0][1]]:
        emit[-1, 0] += 0.5

    trans_bar = np.zeros((n_ch, n_ch))
    trans_mid = np.zeros((n_ch, n_ch))
    for i, ci in enumerate(table):
        for j, cj in enumerate(table):
            if i == j:
                trans_bar[i, j] = -0.1
                trans_mid[i, j] = 0.25
            else:
                trans_bar[i, j] = moves.get((ci[2], cj[2]), 0.0)
                trans_mid[i, j] = moves.get((ci[2], cj[2]), 0.0) - 0.6

    score = emit[0].copy()
    back = np.zeros((n_seg, n_ch), dtype=int)
    for s in range(1, n_seg):
        trans = trans_bar if s % per_bar == 0 else trans_mid
        cand = score[:, None] + trans
        back[s] = cand.argmax(axis=0)
        score = cand.max(axis=0) + emit[s]
    path = [int(score.argmax())]
    for s in range(n_seg - 1, 0, -1):
        path.append(int(back[s, path[-1]]))
    path.reverse()

    def make(c: int, start: float, dur: float) -> Chord:
        off, quality, degree, _ = table[c]
        root = (key.tonic + off) % 12
        return Chord(start, dur, root, quality, key.note_name(root) + QUALITY_SUFFIX[quality], degree)

    chords: list[Chord] = []
    for s, c in enumerate(path):
        same_bar = chords and s % per_bar != 0
        if same_bar and chords[-1].degree == table[c][2]:
            chords[-1].dur += seg
        else:
            chords.append(make(c, s * seg, seg))
    chords.append(make(0, n_bars * beats_per_bar, float(beats_per_bar)))
    assert chords[-1].degree == tonic_degree
    return chords
