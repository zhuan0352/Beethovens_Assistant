"""Synthwave: saw lead with delay, pad, 16th-note arpeggio, pulsing bass and a
drum machine that side-chains the pad and bass."""

from __future__ import annotations

from .common import (
    DRUM_CLAP, DRUM_HAT, DRUM_KICK, DRUM_OPEN_HAT, DRUM_SNARE, Context, Track, bass_pitch,
    fit_register, humanize, voice_chord,
)

META = {
    "id": "synth",
    "name": "Synth",
    "tagline": "Synthwave — saw lead, arpeggios and a four-on-the-floor pulse",
    "reverb": {"seconds": 2.6, "damping": 7500, "level": 0.8},
}


def arrange(ctx: Context) -> list[Track]:
    bpb = ctx.beats_per_bar
    body_bars = ctx.bars - 1
    end = ctx.ending
    mel = fit_register(ctx.melody, center=72, lo=60, hi=88)

    lead = Track(
        "melody", "Lead", "synth_lead", 81, gain=0.8, pan=0.0, reverb=0.3, role="melody",
        fx={"delay": {"beats": 0.75, "feedback": 0.32, "mix": 0.24}},
    )
    for n in mel:
        lead.add(n.pitch, n.start, n.dur * 0.96, n.vel * 0.9 + 8 * ctx.intensity(n.start))

    pad = Track("pad", "Pad", "synth_pad", 89, gain=0.5, pan=0.0, reverb=0.45, fx={"duck": 0.5})
    prev = None
    for chord in ctx.chords:
        voicing = voice_chord(chord, 55, 72, prev)
        prev = voicing
        for p in voicing:
            pad.add(p, chord.start, chord.dur, 62 + 10 * ctx.intensity(chord.start))

    arp = Track("arp", "Arpeggio", "synth_arp", 80, gain=0.42, pan=0.3, reverb=0.3,
                fx={"delay": {"beats": 0.5, "feedback": 0.25, "mix": 0.18}})
    step = 0.5 if ctx.bpm > 140 else 0.25
    shape = [0, 1, 2, 3, 4, 3, 2, 1]
    for chord in ctx.body:
        r = bass_pitch(chord.root, 60)
        third, fifth = chord.intervals[1], chord.intervals[2]
        tones = [r, r + third, r + fifth, r + 12, r + 12 + third]
        t, i = chord.start, 0
        while t < chord.end - 1e-6:
            on_beat = abs(t - round(t)) < 1e-6
            arp.add(tones[shape[i % len(shape)]], t, step * 0.85, (72 if on_beat else 56) + 8 * ctx.intensity(t))
            t += step
            i += 1
    arp.add(bass_pitch(end.root, 72), end.start, 1.0, 70)

    bass = Track("bass", "Bass", "synth_bass", 38, gain=0.8, pan=0.0, reverb=0.03, role="bass", fx={"duck": 0.35})
    for chord in ctx.body:
        root = bass_pitch(chord.root, 33)
        t = chord.start
        while t < chord.end - 1e-6:
            on_beat = abs(t - round(t)) < 1e-6
            bass.add(root, t, 0.42, 92 if on_beat else 78)
            t += 0.5
    bass.add(bass_pitch(end.root, 33), end.start, end.dur, 92)

    drums = Track("drums", "Drum machine", "drums", 0, gain=0.8, pan=0.0, reverb=0.1, role="drums")
    for bar in range(body_bars):
        b0 = bar * bpb
        last_bar = bar == body_bars - 1
        for beat in range(bpb):
            drums.add(DRUM_KICK, b0 + beat, 0.25, 112)
            backbeat = beat % 2 == 1 if bpb == 4 else beat == bpb - 1
            if backbeat:
                drums.add(DRUM_SNARE, b0 + beat, 0.25, 98)
                drums.add(DRUM_CLAP, b0 + beat, 0.25, 72)
            open_hat = bar % 2 == 1 and beat == bpb - 1
            drums.add(DRUM_OPEN_HAT if open_hat else DRUM_HAT, b0 + beat + 0.5, 0.25, 70 if open_hat else 64)
            drums.add(DRUM_HAT, b0 + beat + 0.25, 0.1, 34)
            drums.add(DRUM_HAT, b0 + beat + 0.75, 0.1, 38)
        if last_bar and body_bars >= 2:  # snare fill into the ending
            for k in range(4):
                drums.add(DRUM_SNARE, b0 + bpb - 1 + k * 0.25, 0.2, 70 + 10 * k)
    drums.add(DRUM_KICK, end.start, 0.25, 115)
    drums.add(DRUM_CLAP, end.start, 0.25, 80)

    humanize(lead, ctx.rng, timing=0.004)
    humanize(pad, ctx.rng, timing=0.0, velocity=3)
    humanize(arp, ctx.rng, timing=0.0, velocity=4)
    humanize(bass, ctx.rng, timing=0.0, velocity=3)
    humanize(drums, ctx.rng, timing=0.0, velocity=3)
    return [lead, pad, arp, bass, drums]
