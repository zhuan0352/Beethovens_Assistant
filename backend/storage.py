"""Projects on disk: data/projects/<id>/{project.json, input.wav}.

On a public deployment the oldest projects are pruned so the disk can't fill
up (MAX_PROJECTS, default 200; 0 keeps everything)."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR") or Path(__file__).resolve().parent.parent / "data") / "projects"
MAX_PROJECTS = int(os.environ.get("MAX_PROJECTS", "200"))
_ID = re.compile(r"^[0-9a-f]{12}$")


class ProjectNotFound(KeyError):
    pass


def _dir(project_id: str) -> Path:
    if not _ID.match(project_id):
        raise ProjectNotFound(project_id)
    return DATA_DIR / project_id


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def save(project: dict, wav: bytes | None = None) -> None:
    d = _dir(project["id"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text(json.dumps(project))
    if wav is not None:
        (d / "input.wav").write_bytes(wav)
        _prune(keep=d)


def _prune(keep: Path) -> None:
    if MAX_PROJECTS <= 0:
        return
    dirs = [p for p in DATA_DIR.iterdir() if p.is_dir() and _ID.match(p.name)]
    if len(dirs) <= MAX_PROJECTS:
        return
    dirs.sort(key=lambda p: p.stat().st_mtime)
    for old in dirs[: len(dirs) - MAX_PROJECTS]:
        if old != keep:
            shutil.rmtree(old, ignore_errors=True)


def load(project_id: str) -> dict:
    path = _dir(project_id) / "project.json"
    if not path.exists():
        raise ProjectNotFound(project_id)
    return json.loads(path.read_text())


def audio_path(project_id: str) -> Path:
    path = _dir(project_id) / "input.wav"
    if not path.exists():
        raise ProjectNotFound(project_id)
    return path
