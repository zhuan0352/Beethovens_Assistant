"""Solo piano: melody in the right hand over broken-chord left hand with pedal."""

from __future__ import annotations

from .common import Context, Track, bass_pitch, fit_register, humanize, lowest_melody_pitch, voice_chord

META = {
    "id": "piano",
    "name": "Piano",
    "tagline": "Solo piano ballad — broken chords under the tune",
    "reverb": {"seconds": 2.2, "damping": 5200, "level": 0.9},
}


def arrange(ctx: Context) -> list[Track]:
    mel = fit_register(ctx.melody, center=74, lo=60, hi=93)

    lead = Track("melody", "Melody", "piano", 0, gain=0.95, pan=0.1, reverb=0.26, role="melody")
    for n in mel:
        lead.add(n.pitch, n.start, n.dur, n.vel * 0.9 + 8 * ctx.intensity(n.start))

    left = Track("left_hand", "Left hand", "piano", 0, gain=0.72, pan=-0.15, reverb=0.26, role="bass")
    step = 1.0 if ctx.bpm > 132 else 0.5
    pattern = [0, 1, 2, 3, 2, 1, 2, 1]
    for chord in ctx.body:
        root = bass_pitch(chord.root, 36)
        third, fifth = chord.intervals[1], chord.intervals[2]
        tones = [root, root + fifth, root + 12, root + 12 + third]
        t, i = chord.start, 0
        while t < chord.end - 1e-6:
            sustain = min(chord.end - t, 2.0)  # sustain pedal, cleared at each chord change
            left.add(tones[pattern[i % len(pattern)]], t, sustain, 58 + (12 if i == 0 else 0) + 10 * ctx.intensity(t))
            t += step
            i += 1

    inner = Track("chords", "Inner chords", "piano", 0, gain=0.5, pan=0.0, reverb=0.3)
    prev = None
    for chord in ctx.body:
        top = lowest_melody_pitch(mel, chord.start, chord.end)
        voicing = voice_chord(chord, 55, 72, prev, top=top - 1 if top else None)
        prev = voicing
        for p in voicing:
            inner.add(p, chord.start, chord.dur, 44 + 10 * ctx.intensity(chord.start))

    # ending: a rolled tonic chord left to ring
    end = ctx.ending
    root = bass_pitch(end.root, 36)
    third = end.intervals[1]
    roll = [root, root + 7, root + 12, root + 12 + third, root + 19, root + 24]
    gap = ctx.seconds(0.045)
    for i, p in enumerate(roll):
        (left if i < 3 else inner).add(p, end.start + i * gap, end.dur - i * gap, 66 - 2 * i)

    for track in (lead, left, inner):
        humanize(track, ctx.rng)
    return [lead, inner, left]
