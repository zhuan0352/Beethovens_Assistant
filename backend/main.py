"""Beethoven Assistant API.

    POST /api/projects                    upload a hummed take (WAV) -> full project
    GET  /api/projects/{id}               fetch a project
    POST /api/projects/{id}/rearrange     re-run with a different tempo / key / metre
    GET  /api/projects/{id}/midi/{style}  download one arrangement as a .mid
    GET  /api/projects/{id}/audio         the original take
    GET  /api/styles                      available arrangement styles
    GET  /api/demo/hum.wav                a synthetic hummed melody to try

The browser renders audio itself from the note data, so the server never has
to synthesise or store rendered tracks.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import storage
from .pipeline import NoMelodyError, arrange_project, transcribe
from .pipeline.arrange import style_list
from .pipeline.audio import ANALYSIS_SR, AudioError, read_wav, write_wav
from .pipeline.demo import synth_hum
from .pipeline.midi import write_midi

MAX_UPLOAD_BYTES = 30 * 1024 * 1024
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="Beethoven Assistant", version="0.1.0")


@app.middleware("http")
async def revalidate_frontend(request, call_next):
    """Frontend files aren't fingerprinted, so make browsers revalidate them."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


class KeyChoice(BaseModel):
    tonic: int = Field(ge=0, le=11)
    mode: Literal["major", "minor"]


class RearrangeRequest(BaseModel):
    """Omitted / null fields fall back to what the analysis detected."""

    bpm: float | None = Field(default=None, ge=40, le=240)
    key: KeyChoice | None = None
    beats_per_bar: Literal[3, 4] = 4


def _public(project: dict) -> dict:
    return {k: v for k, v in project.items() if k != "transcription"} | {
        "duration": project["transcription"]["duration"],
        "tuning_cents": project["transcription"]["tuning_cents"],
        "contour": project["transcription"]["contour"],
        "raw_notes": project["transcription"]["raw_notes"],
    }


def _load(project_id: str) -> dict:
    try:
        return storage.load(project_id)
    except storage.ProjectNotFound:
        raise HTTPException(404, "Project not found.") from None


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/styles")
def styles() -> list[dict]:
    return style_list()


@app.get("/api/demo/hum.wav")
def demo_hum() -> Response:
    return Response(write_wav(synth_hum(), ANALYSIS_SR), media_type="audio/wav")


@app.post("/api/projects")
def create_project(
    audio: UploadFile = File(...),
    name: str = Form("Untitled idea"),
    beats_per_bar: int = Form(4),
) -> dict:
    if beats_per_bar not in (3, 4):
        raise HTTPException(422, "beats_per_bar must be 3 or 4.")
    data = audio.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Recording is too large.")
    try:
        samples, sr = read_wav(data)
        transcription = transcribe(samples, sr)
    except (AudioError, NoMelodyError) as exc:
        raise HTTPException(422, str(exc)) from None

    project = {
        "id": storage.new_id(),
        "name": name.strip()[:80] or "Untitled idea",
        "created": int(time.time()),
        "transcription": transcription,
        "settings": {"bpm": None, "key": None, "beats_per_bar": beats_per_bar},
        **arrange_project(transcription, beats_per_bar=beats_per_bar),
    }
    storage.save(project, wav=data)
    return _public(project)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict:
    return _public(_load(project_id))


@app.post("/api/projects/{project_id}/rearrange")
def rearrange(project_id: str, req: RearrangeRequest) -> dict:
    project = _load(project_id)
    key = req.key.model_dump() if req.key else None
    project["settings"] = {"bpm": req.bpm, "key": key, "beats_per_bar": req.beats_per_bar}
    project.update(arrange_project(project["transcription"], bpm=req.bpm, key=key, beats_per_bar=req.beats_per_bar))
    storage.save(project)
    return _public(project)


@app.get("/api/projects/{project_id}/midi/{style}")
def midi(project_id: str, style: str) -> Response:
    project = _load(project_id)
    arrangement = project["arrangements"].get(style)
    if arrangement is None:
        raise HTTPException(404, "Unknown style.")
    data = write_midi(
        arrangement["tracks"], project["bpm"], project["beats_per_bar"],
        title=f"{project['name']} ({arrangement['name']})",
    )
    return Response(
        data,
        media_type="audio/midi",
        headers={"Content-Disposition": f'attachment; filename="beethoven-{style}.mid"'},
    )


@app.get("/api/projects/{project_id}/audio")
def original_audio(project_id: str) -> FileResponse:
    try:
        return FileResponse(storage.audio_path(project_id), media_type="audio/wav")
    except storage.ProjectNotFound:
        raise HTTPException(404, "Project not found.") from None


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
