"""Acoustic guitar band: picked lead, strummed chords, bass and light percussion."""

from __future__ import annotations

from ..harmony import Chord
from .common import (
    DRUM_KICK, DRUM_RIM, DRUM_SHAKER, Context, Track, bass_pitch, fit_register, humanize,
)

META = {
    "id": "guitar",
    "name": "Guitar",
    "tagline": "Acoustic — fingerpicked lead over strummed chords",
    "reverb": {"seconds": 1.3, "damping": 6500, "level": 0.7},
}

# (beat offset, direction, accent)
STRUM_44 = [(0, "d", 1.0), (1, "d", 0.8), (1.5, "u", 0.6), (2.5, "u", 0.65), (3, "d", 0.85), (3.5, "u", 0.6)]
STRUM_34 = [(0, "d", 1.0), (1, "d", 0.75), (1.5, "u", 0.6), (2, "d", 0.8), (2.5, "u", 0.6)]


def _voicing(chord: Chord) -> list[int]:
    """Six-string shape: root, fifth, octave, third, fifth, double octave."""
    r = bass_pitch(chord.root, 40)  # low E string upward
    third, fifth = chord.intervals[1], chord.intervals[2]
    strings = [r, r + fifth, r + 12, r + 12 + third, r + 12 + fifth, r + 24]
    return [p for p in strings if p <= 76]


def arrange(ctx: Context) -> list[Track]:
    bpb = ctx.beats_per_bar
    mel = fit_register(ctx.melody, center=67, lo=55, hi=86)

    lead = Track("melody", "Lead guitar", "nylon_guitar", 24, gain=1.0, pan=0.12, reverb=0.22, role="melody")
    for n in mel:
        lead.add(n.pitch, n.start, n.dur + 0.15, n.vel * 0.92 + 6 * ctx.intensity(n.start))

    strum = Track("strum", "Strummed guitar", "steel_guitar", 25, gain=0.55, pan=-0.3, reverb=0.18)
    pattern = STRUM_44 if bpb == 4 else STRUM_34 if bpb == 3 else [(b, "d", 1.0 if b == 0 else 0.75) for b in range(bpb)]
    gap = ctx.seconds(0.016)
    body_bars = ctx.bars - 1
    for bar in range(body_bars):
        for i, (off, direction, accent) in enumerate(pattern):
            t = bar * bpb + off
            nxt = bar * bpb + pattern[i + 1][0] if i + 1 < len(pattern) else (bar + 1) * bpb
            strings = _voicing(ctx.chord_at(t))
            if direction == "u":
                strings = strings[-4:][::-1]
            ring = (nxt - t) + 0.35
            base = (56 + 22 * accent) * (0.9 if direction == "u" else 1.0) + 8 * ctx.intensity(t)
            for s, p in enumerate(strings):
                strum.add(p, t + s * gap, ring - s * gap, base - 1.5 * s)

    bass = Track("bass", "Bass", "acoustic_bass", 32, gain=0.85, pan=0.0, reverb=0.05, role="bass")
    for chord in ctx.body:
        root = bass_pitch(chord.root, 28)
        fifth = root + chord.intervals[2]
        bass.add(root, chord.start, min(1.4, chord.dur - 0.1), 92)
        if chord.dur >= 2:
            bass.add(root, chord.start + 1.5, 0.45, 76)
        if chord.dur == 3:
            bass.add(fifth, chord.start + 2, 0.9, 82)
        if chord.dur >= 4:
            bass.add(fifth, chord.start + 2, 0.9, 84)
            bass.add(root, chord.start + 3, 0.9, 80)

    perc = Track("percussion", "Percussion", "drums", 0, gain=0.5, pan=0.0, reverb=0.12, role="drums")
    for bar in range(body_bars):
        full_kit = bar >= 1 or body_bars <= 2  # shaker alone for the first bar
        for eighth in range(bpb * 2):
            t = bar * bpb + eighth * 0.5
            perc.add(DRUM_SHAKER, t, 0.2, 66 if eighth % 2 else 48)
        if full_kit:
            for beat in range(bpb):
                on_backbeat = beat % 2 == 1 if bpb == 4 else beat > 0
                perc.add(DRUM_RIM if on_backbeat else DRUM_KICK, bar * bpb + beat, 0.25, 62 if on_backbeat else 84)

    # ending: one slow strum, left to ring
    end = ctx.ending
    for s, p in enumerate(_voicing(end)):
        strum.add(p, end.start + s * ctx.seconds(0.035), end.dur, 78 - s)
    bass.add(bass_pitch(end.root, 28), end.start, end.dur, 90)
    perc.add(DRUM_KICK, end.start, 0.25, 80)

    humanize(lead, ctx.rng)
    humanize(strum, ctx.rng, timing=0.004)
    humanize(bass, ctx.rng)
    humanize(perc, ctx.rng, timing=0.004, velocity=4)
    return [lead, strum, bass, perc]
