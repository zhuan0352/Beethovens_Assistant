"""Orchestral: violins carry the tune, strings sustain the harmony, harp keeps
time; flute and horns join halfway to build, timpani marks the phrases."""

from __future__ import annotations

from .common import (
    Context, Track, bass_pitch, fit_register, humanize, legato, lowest_melody_pitch, voice_chord,
)

META = {
    "id": "orchestral",
    "name": "Orchestral",
    "tagline": "Strings, horns, harp and timpani — builds to the final bar",
    "reverb": {"seconds": 3.2, "damping": 4800, "level": 1.0},
}


def arrange(ctx: Context) -> list[Track]:
    bpb = ctx.beats_per_bar
    body_bars = ctx.bars - 1
    end = ctx.ending
    build_from = (body_bars // 2) * bpb if body_bars >= 4 else 0.0
    mel = legato(fit_register(ctx.melody, center=76, lo=64, hi=96), max_gap=0.5)

    violins = Track("melody", "Violins I", "strings", 48, gain=0.85, pan=-0.3, reverb=0.42, role="melody")
    for n in mel:
        violins.add(n.pitch, n.start, n.dur, n.vel * 0.85 + 14 * ctx.intensity(n.start))

    flute = Track("flute", "Flute", "flute", 73, gain=0.4, pan=0.2, reverb=0.45, role="melody")
    for n in mel:
        if n.start >= build_from - 1e-6:
            flute.add(n.pitch + 12 if n.pitch + 12 <= 98 else n.pitch, n.start, n.dur, n.vel * 0.8)

    violas = Track("violas", "Violins II & violas", "strings", 48, gain=0.55, pan=0.1, reverb=0.45)
    prev = None
    for chord in ctx.chords:
        top = lowest_melody_pitch(mel, chord.start, chord.end)
        voicing = voice_chord(chord, 55, 74, prev, top=top - 1 if top else None)
        prev = voicing
        for p in voicing:
            violas.add(p, chord.start, chord.dur, 54 + 22 * ctx.intensity(chord.start))

    cellos = Track("cellos", "Cellos", "cello", 42, gain=0.65, pan=0.3, reverb=0.4, role="bass")
    basses = Track("basses", "Double basses", "contrabass", 43, gain=0.65, pan=0.4, reverb=0.35, role="bass")
    for chord in ctx.chords:
        vel = 60 + 20 * ctx.intensity(chord.start)
        cellos.add(bass_pitch(chord.root, 43), chord.start, chord.dur, vel)
        basses.add(bass_pitch(chord.root, 28), chord.start, chord.dur, vel)

    horns = Track("horns", "French horns", "horn", 60, gain=0.5, pan=-0.15, reverb=0.5)
    prev = None
    for chord in ctx.chords:
        if chord.start < build_from - 1e-6:
            continue
        voicing = voice_chord(chord, 50, 67, prev)
        prev = voicing
        for p in voicing[:2]:
            horns.add(p, chord.start, chord.dur, 56 + 16 * ctx.intensity(chord.start))

    harp = Track("harp", "Harp", "harp", 46, gain=0.5, pan=0.35, reverb=0.4)
    shape = [0, 1, 2, 3, 4, 5, 4, 3]
    for chord in ctx.body:
        r = bass_pitch(chord.root, 48)
        third, fifth = chord.intervals[1], chord.intervals[2]
        tones = [r, r + fifth, r + 12, r + 12 + third, r + 12 + fifth, r + 24]
        t, i = chord.start, 0
        while t < chord.end - 1e-6:
            harp.add(tones[shape[i % len(shape)]], t, 1.5, 52 + (8 if i == 0 else 0))
            t += 0.5
            i += 1
    r = bass_pitch(end.root, 36)
    gliss = [r, r + 7, r + 12, r + 12 + end.intervals[1], r + 19, r + 24, r + 24 + end.intervals[1], r + 31]
    for i, p in enumerate(gliss):
        harp.add(p, end.start + i * ctx.seconds(0.05), end.dur, 64)

    timpani = Track("timpani", "Timpani", "timpani", 47, gain=0.7, pan=0.0, reverb=0.5, role="drums")
    for bar in range(4, body_bars, 4):
        chord = ctx.chord_at(bar * bpb)
        timpani.add(bass_pitch(chord.root, 41), bar * bpb, 1.0, 70 + 20 * ctx.intensity(bar * bpb))
    if body_bars >= 2:  # roll into the final chord
        last = ctx.chord_at(end.start - 0.5)
        for k in range(8):
            timpani.add(bass_pitch(last.root, 41), end.start - 2 + k * 0.25, 0.3, 46 + 7 * k)
    timpani.add(bass_pitch(end.root, 41), end.start, 2.0, 112)

    humanize(violins, ctx.rng, timing=0.006)
    humanize(flute, ctx.rng, timing=0.006)
    for track in (violas, cellos, basses, horns):
        humanize(track, ctx.rng, timing=0.01, velocity=3)
    humanize(harp, ctx.rng)
    return [violins, flute, violas, horns, cellos, basses, harp, timpani]
