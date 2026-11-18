"""Minimal Standard MIDI File (format 1) writer."""

from __future__ import annotations

import struct

TICKS = 480
DRUM_CHANNEL = 9


def _varlen(value: int) -> bytes:
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def _chunk(events: list[tuple[int, bytes]]) -> bytes:
    """events: (absolute tick, message bytes). Note-offs sort before note-ons at the same tick."""
    events.sort(key=lambda e: (e[0], 0 if e[1][0] & 0xF0 == 0x80 else 1))
    body = bytearray()
    last = 0
    for tick, msg in events:
        body += _varlen(tick - last) + msg
        last = tick
    body += b"\x00\xff\x2f\x00"
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def _meta(kind: int, data: bytes) -> bytes:
    return bytes([0xFF, kind]) + _varlen(len(data)) + data


def write_midi(tracks: list[dict], bpm: float, beats_per_bar: int, title: str = "") -> bytes:
    tempo = int(round(60_000_000 / bpm))
    conductor = [
        (0, _meta(0x03, title.encode("ascii", "replace"))),
        (0, _meta(0x51, tempo.to_bytes(3, "big"))),
        (0, _meta(0x58, bytes([beats_per_bar, 2, 24, 8]))),
    ]
    chunks = [_chunk(conductor)]

    free_channels = [c for c in range(16) if c != DRUM_CHANNEL]
    for i, track in enumerate(tracks):
        is_drums = track["instrument"] == "drums"
        channel = DRUM_CHANNEL if is_drums else free_channels[i % len(free_channels)]
        events = [(0, _meta(0x03, track["name"].encode("ascii", "replace")))]
        if not is_drums:
            events.append((0, bytes([0xC0 | channel, track["program"] & 0x7F])))
        for pitch, start, dur, vel in track["notes"]:
            on = int(round(start * TICKS))
            off = max(on + 1, int(round((start + dur) * TICKS)))
            events.append((on, bytes([0x90 | channel, pitch & 0x7F, vel & 0x7F])))
            events.append((off, bytes([0x80 | channel, pitch & 0x7F, 0])))
        chunks.append(_chunk(events))

    header = b"MThd" + struct.pack(">IHHH", 6, 1, len(chunks), TICKS)
    return header + b"".join(chunks)
