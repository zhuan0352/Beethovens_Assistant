"""WAV decoding and resampling. The browser sends 16-bit mono PCM, but any
integer-PCM WAV is accepted so the API is usable from scripts too."""

from __future__ import annotations

import io
import wave

import numpy as np

ANALYSIS_SR = 16000
MAX_SECONDS = 90.0


class AudioError(ValueError):
    pass


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Decode a PCM WAV into mono float32 in [-1, 1]."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            sr = w.getframerate()
            channels = w.getnchannels()
            width = w.getsampwidth()
            raw = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as exc:
        raise AudioError(f"Not a readable PCM WAV file ({exc}).") from exc

    if width == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        x = v.astype(np.float32) / float(1 << 23)
    elif width == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / float(1 << 31)
    else:
        raise AudioError(f"Unsupported sample width: {width} bytes.")

    if channels > 1:
        x = x[: len(x) // channels * channels].reshape(-1, channels).mean(axis=1)
    if len(x) == 0:
        raise AudioError("The recording is empty.")
    if len(x) / sr > MAX_SECONDS:
        raise AudioError(f"Recording is longer than {int(MAX_SECONDS)} seconds.")
    return x.astype(np.float32), sr


def resample(x: np.ndarray, sr: int, target: int = ANALYSIS_SR) -> np.ndarray:
    """Linear resampling with a box prefilter when downsampling. Pitch tracking
    only needs the band below ~2 kHz, so this is plenty."""
    if sr == target:
        return x
    ratio = sr / target
    if ratio > 1.0:
        k = int(round(ratio))
        if k > 1:
            x = np.convolve(x, np.ones(k, dtype=np.float32) / k, mode="same")
    n_out = int(len(x) / ratio)
    pos = np.arange(n_out) * ratio
    return np.interp(pos, np.arange(len(x)), x).astype(np.float32)


def write_wav(x: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    pcm = (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
