"""End-to-end checks against the synthetic hum, whose notes, tempo and key are known."""

import struct

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import storage
from backend.main import app
from backend.pipeline import NoMelodyError, arrange_project, transcribe
from backend.pipeline.audio import ANALYSIS_SR, write_wav
from backend.pipeline.demo import DEMO_BPM, DEMO_TUNING_CENTS, ODE_TO_JOY, synth_hum
from backend.pipeline.midi import write_midi


@pytest.fixture(scope="module")
def transcription():
    return transcribe(synth_hum(), ANALYSIS_SR)


@pytest.fixture(scope="module")
def project(transcription):
    return arrange_project(transcription)


def test_transcribes_every_note(transcription):
    assert [n["pitch"] for n in transcription["raw_notes"]] == [p for p, _ in ODE_TO_JOY]
    assert abs(transcription["tuning_cents"] - DEMO_TUNING_CENTS) < 10


def test_finds_tempo_key_and_rhythm(project):
    assert abs(project["bpm"] - DEMO_BPM) < 2
    assert project["key"]["name"] == "C major"
    assert [n[2] for n in project["melody"]] == [d for _, d in ODE_TO_JOY]
    assert project["melody"][0][1] == 0


def test_chords_cover_the_piece_and_resolve(project):
    chords = project["chords"]
    assert chords[0]["start"] == 0
    for a, b in zip(chords, chords[1:]):
        assert a["start"] + a["dur"] == pytest.approx(b["start"])
    assert chords[0]["degree"] == "I" and chords[-1]["degree"] == "I"
    assert chords[-1]["start"] + chords[-1]["dur"] == project["total_beats"]


def test_every_style_plays_the_same_melody(project):
    want = [n[0] % 12 for n in project["melody"]]
    assert set(project["arrangements"]) == {"piano", "guitar", "synth", "orchestral"}
    for style, arrangement in project["arrangements"].items():
        lead = next(t for t in arrangement["tracks"] if t["id"] == "melody")
        assert [n[0] % 12 for n in lead["notes"]] == want, style
        assert len(arrangement["tracks"]) >= 3, style
        for track in arrangement["tracks"]:
            for pitch, start, dur, vel in track["notes"]:
                assert 0 <= pitch <= 127 and start >= 0 and dur > 0 and 1 <= vel <= 127
                assert start < project["total_beats"]


def test_tempo_key_and_metre_overrides(transcription):
    forced = arrange_project(transcription, bpm=56, key={"tonic": 9, "mode": "minor"}, beats_per_bar=3)
    assert forced["bpm"] == 56
    assert forced["key"]["name"] == "A minor"
    assert forced["detected_key"]["name"] == "C major"
    assert forced["total_beats"] % 3 == 0
    assert forced["chords"][-1]["name"] == "Am"


def test_midi_export_is_well_formed(project):
    tracks = project["arrangements"]["synth"]["tracks"]
    data = write_midi(tracks, project["bpm"], 4, "test")
    assert data[:4] == b"MThd"
    _, fmt, n_tracks, ticks = struct.unpack(">IHHH", data[4:14])
    assert (fmt, n_tracks, ticks) == (1, len(tracks) + 1, 480)
    pos, found = 14, 0
    while pos < len(data):
        assert data[pos : pos + 4] == b"MTrk"
        (length,) = struct.unpack(">I", data[pos + 4 : pos + 8])
        assert data[pos + 8 + length - 3 : pos + 8 + length] == b"\xff\x2f\x00"
        pos += 8 + length
        found += 1
    assert found == n_tracks


def test_silence_and_noise_are_rejected():
    with pytest.raises(NoMelodyError):
        transcribe(np.zeros(ANALYSIS_SR * 3, dtype=np.float32), ANALYSIS_SR)
    noise = np.random.default_rng(0).normal(0, 0.1, ANALYSIS_SR * 3).astype(np.float32)
    with pytest.raises(NoMelodyError):
        transcribe(noise, ANALYSIS_SR)


def test_api_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    client = TestClient(app)
    wav = client.get("/api/demo/hum.wav").content

    created = client.post("/api/projects", files={"audio": ("take.wav", wav, "audio/wav")})
    assert created.status_code == 200
    project = created.json()
    assert "transcription" not in project and len(project["melody"]) == len(ODE_TO_JOY)

    pid = project["id"]
    assert client.get(f"/api/projects/{pid}").json()["bpm"] == project["bpm"]
    again = client.post(f"/api/projects/{pid}/rearrange", json={"bpm": 90, "beats_per_bar": 4})
    assert again.json()["bpm"] == 90
    assert client.get(f"/api/projects/{pid}/midi/orchestral").content[:4] == b"MThd"
    assert client.get(f"/api/projects/{pid}/audio").content == wav

    assert client.get(f"/api/projects/{pid}/midi/kazoo").status_code == 404
    assert client.get("/api/projects/../../etc").status_code == 404
    assert client.get("/api/projects/000000000000").status_code == 404
    silent = write_wav(np.zeros(ANALYSIS_SR * 2, dtype=np.float32), ANALYSIS_SR)
    assert client.post("/api/projects", files={"audio": ("s.wav", silent, "audio/wav")}).status_code == 422
    assert client.post("/api/projects", files={"audio": ("x.wav", b"not audio", "audio/wav")}).status_code == 422


# (melody, bpm, tuning offset in cents, beats per bar, expected key)
VARIED_TAKES = {
    "minor, eighth notes": (
        [(57, 0.5), (60, 0.5), (64, 0.5), (60, 0.5), (57, 1), (55, 1), (53, 0.5), (57, 0.5), (60, 0.5), (57, 0.5),
         (52, 2), (57, 0.5), (60, 0.5), (64, 0.5), (60, 0.5), (62, 1), (59, 1), (57, 3)],
        96, -28, 4, "A minor",
    ),
    "fast, high voice": (
        [(67, 1), (71, 1), (74, 1), (71, 1), (72, 1), (69, 1), (66, 2), (67, 1), (71, 1), (74, 1), (79, 1), (78, 1),
         (74, 1), (67, 2)],
        150, 12, 4, "G major",
    ),
    "slow, low voice": (
        [(41, 1), (45, 1), (48, 2), (46, 1), (45, 1), (43, 2), (41, 1), (43, 1), (45, 1), (48, 1), (41, 4)],
        70, 45, 4, "F major",
    ),
    "waltz with wide leaps": (
        [(62, 1), (66, 1), (69, 1), (69, 2), (66, 1), (64, 1), (67, 1), (71, 1), (69, 3), (62, 1), (66, 1), (69, 1),
         (74, 2), (73, 1), (71, 1), (69, 1), (64, 1), (62, 3)],
        120, -10, 3, "D major",
    ),
}


@pytest.mark.parametrize("name", VARIED_TAKES)
@pytest.mark.parametrize("seed", [3, 11])
def test_varied_takes(name, seed):
    """Different registers, tempos, metres and tunings, in a noisier room.
    Slides between notes must not turn into extra notes."""
    melody, bpm, cents, beats_per_bar, key = VARIED_TAKES[name]
    x = synth_hum(melody, bpm=bpm, tuning_cents=cents, seed=seed)
    x = x + np.random.default_rng(1).normal(0, 0.01, len(x)).astype(np.float32)
    transcription = transcribe(x, ANALYSIS_SR)
    assert [n["pitch"] for n in transcription["raw_notes"]] == [p for p, _ in melody]
    result = arrange_project(transcription, beats_per_bar=beats_per_bar)
    assert abs(result["bpm"] - bpm) < 0.02 * bpm
    assert result["key"]["name"] == key
