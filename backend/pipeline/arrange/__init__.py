"""Arrangement styles. Each style turns the same melody + chords into its own
set of instrument tracks, so versions can be compared like-for-like.

To add a style: write a module with META and arrange(ctx) -> list[Track],
and register it in STYLES. The browser engine needs a voice for every
`instrument` name the tracks use (see frontend/js/engine/instruments.js)."""

from __future__ import annotations

from . import guitar, orchestral, piano, synth
from .common import Context

STYLES = [piano, guitar, synth, orchestral]


def style_list() -> list[dict]:
    return [{k: m.META[k] for k in ("id", "name", "tagline")} for m in STYLES]


def build_all(melody, chords, key, bpm: float, beats_per_bar: int) -> dict[str, dict]:
    seed = hash(tuple((n.pitch, n.start, n.dur) for n in melody)) & 0xFFFFFFFF
    out = {}
    for module in STYLES:
        ctx = Context(melody, chords, key, bpm, beats_per_bar, seed=seed)
        tracks = [t for t in module.arrange(ctx) if t.notes]
        out[module.META["id"]] = {**module.META, "tracks": [t.to_dict() for t in tracks]}
    return out
