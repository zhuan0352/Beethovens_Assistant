"""A synthetic "hummed" melody for trying the app without a microphone and for
testing the pipeline against a known answer. It is deliberately imperfect:
sung low, ~35 cents sharp of concert pitch, with glides, vibrato, loose timing
and background noise."""

from __future__ import annotations

import numpy as np

# Ode to Joy, first two phrases: (MIDI pitch, beats). Hummed an octave below middle C.
ODE_TO_JOY = [
    (52, 1), (52, 1), (53, 1), (55, 1), (55, 1), (53, 1), (52, 1), (50, 1),
    (48, 1), (48, 1), (50, 1), (52, 1), (52, 1.5), (50, 0.5), (50, 2),
    (52, 1), (52, 1), (53, 1), (55, 1), (55, 1), (53, 1), (52, 1), (50, 1),
    (48, 1), (48, 1), (50, 1), (52, 1), (50, 1.5), (48, 0.5), (48, 2),
]
DEMO_BPM = 112.0
DEMO_KEY = (0, "major")
DEMO_TUNING_CENTS = 35.0


def synth_hum(
    melody: list[tuple[int, float]] = ODE_TO_JOY,
    bpm: float = DEMO_BPM,
    sr: int = 16000,
    tuning_cents: float = DEMO_TUNING_CENTS,
    seed: int = 7,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    lead_in, tail = 0.4, 0.6
    total = lead_in + sum(d for _, d in melody) * beat + tail
    n = int(total * sr)
    t = np.arange(n) / sr

    target = np.full(n, melody[0][0] + tuning_cents / 100.0)  # MIDI pitch per sample
    amp = np.zeros(n)
    pos = lead_in
    for pitch, beats in melody:
        dur = beats * beat
        start = pos + rng.normal(0, 0.018)  # loose timing
        end = pos + dur - 0.07  # small breath before the next note
        a, b = int(max(start, 0) * sr), int(min(end, total) * sr)
        target[a:] = pitch + tuning_cents / 100.0 + rng.normal(0, 0.12)
        env = np.ones(b - a)
        attack, release = int(0.035 * sr), int(0.05 * sr)
        env[:attack] = np.linspace(0, 1, attack)
        env[-release:] = np.linspace(1, 0, release)
        amp[a:b] = env * rng.uniform(0.75, 1.0)
        pos += dur

    # glide between notes: one-pole smoothing (~25 ms), run at a 1 kHz control rate
    step = sr // 1000
    control = target[::step]
    alpha = 1.0 - np.exp(-1.0 / 25.0)
    smooth = np.empty(len(control))
    acc = control[0]
    for i, v in enumerate(control):
        acc += alpha * (v - acc)
        smooth[i] = acc
    midi = np.interp(np.arange(n), np.arange(len(control)) * step, smooth)
    midi += 0.18 * np.sin(2 * np.pi * 5.3 * t)  # vibrato

    f0 = 440.0 * 2.0 ** ((midi - 69.0) / 12.0)
    phase = 2 * np.pi * np.cumsum(f0) / sr
    wave = np.zeros(n)
    for h in range(1, 11):  # voice-like harmonic roll-off
        wave += np.sin(h * phase) / h**1.7
    x = 0.35 * amp * wave + rng.normal(0, 0.003, n)
    return x.astype(np.float32)
