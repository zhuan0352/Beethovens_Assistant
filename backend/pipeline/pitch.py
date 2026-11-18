"""Monophonic pitch tracking (YIN, de Cheveigné & Kawahara 2002), vectorised
across frames with an FFT cross-correlation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class PitchTrack:
    times: np.ndarray  # frame centre, seconds
    f0: np.ndarray  # Hz (always a number; check `aperiodicity` for confidence)
    aperiodicity: np.ndarray  # YIN CMNDF value at the chosen lag; lower = more periodic
    rms: np.ndarray
    hop_s: float


def track_pitch(
    x: np.ndarray,
    sr: int,
    hop: int = 160,
    win: int = 1024,
    fmin: float = 65.0,
    fmax: float = 1000.0,
    threshold: float = 0.15,
) -> PitchTrack:
    tau_min = max(2, int(sr / fmax))
    tau_max = int(np.ceil(sr / fmin))
    integ = win - tau_max  # integration length
    if integ < tau_max:
        raise ValueError("window too short for fmin")

    pad = win // 2
    xp = np.pad(x.astype(np.float64), (pad, pad))
    n = 1 + (len(xp) - win) // hop
    nfft = 1 << int(np.ceil(np.log2(win + integ)))
    taus = np.arange(tau_max + 1)

    f0 = np.zeros(n)
    ap = np.ones(n)
    rms = np.zeros(n)

    for s in range(0, n, 1024):
        e = min(n, s + 1024)
        idx = np.arange(win)[None, :] + hop * np.arange(s, e)[:, None]
        fr = xp[idx]
        fr = fr - fr.mean(axis=1, keepdims=True)
        rms[s:e] = np.sqrt((fr**2).mean(axis=1))

        # d(tau) = sum_j (x_j - x_{j+tau})^2 over j in [0, integ)
        a = np.fft.rfft(fr[:, :integ], nfft)
        b = np.fft.rfft(fr, nfft)
        cross = np.fft.irfft(np.conj(a) * b, nfft)[:, : tau_max + 1]
        cs = np.concatenate([np.zeros((e - s, 1)), np.cumsum(fr**2, axis=1)], axis=1)
        energy = cs[:, taus + integ] - cs[:, taus]
        d = np.maximum(energy[:, :1] + energy - 2.0 * cross, 0.0)

        # cumulative mean normalised difference
        cm = np.ones_like(d)
        cum = np.cumsum(d[:, 1:], axis=1)
        cm[:, 1:] = d[:, 1:] * taus[1:] / np.maximum(cum, 1e-12)

        c = cm[:, tau_min : tau_max + 1]
        k_count = c.shape[1]
        local_min = np.zeros_like(c, dtype=bool)
        local_min[:, 1:-1] = (c[:, 1:-1] <= c[:, :-2]) & (c[:, 1:-1] < c[:, 2:])
        cand = local_min & (c < threshold)
        has = cand.any(axis=1)
        first = cand.argmax(axis=1)
        best = np.where(local_min, c, np.inf).argmin(axis=1)
        k = np.clip(np.where(has, first, best), 1, k_count - 2)

        rows = np.arange(e - s)
        y0, y1, y2 = c[rows, k - 1], c[rows, k], c[rows, k + 1]
        denom = y0 - 2.0 * y1 + y2
        shift = np.where(np.abs(denom) > 1e-12, 0.5 * (y0 - y2) / np.where(denom == 0, 1, denom), 0.0)
        shift = np.clip(shift, -1.0, 1.0)
        f0[s:e] = sr / (tau_min + k + shift)
        ap[s:e] = y1

    times = np.arange(n) * hop / sr
    return PitchTrack(times=times, f0=f0, aperiodicity=ap, rms=rms, hop_s=hop / sr)
