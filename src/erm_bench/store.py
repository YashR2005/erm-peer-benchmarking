"""Run store: one folder per run, one JSON artifact per stage.

runs/<run_id>/
  run.json          request, setup version, model, status
  sources/          downloaded documents, named by SHA-256
  pages/            extracted text per page (what quotes are checked against)
  stages/*.json     one artifact per stage
  reviews.jsonl     every human action: who, what, before -> after
  llm_calls.jsonl   model, prompt version, usage, stop reason, output
  exports/
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, TypeAdapter

from .standard_setup import REPO_ROOT

DEFAULT_RUNS_DIR = REPO_ROOT / "runs"

T = TypeVar("T")

RunStatus = str  # researching | awaiting_scope | analysing | awaiting_findings | exported | failed


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:30] or "run"


def _to_jsonable(obj: object) -> object:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, list):
        return [_to_jsonable(o) for o in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj


class RunStore:
    def __init__(self, run_dir: Path):
        self.dir = run_dir
        self._lock = threading.Lock()

    # Creating and opening

    @classmethod
    def create(cls, client_name: str, meta: dict, runs_dir: Path = DEFAULT_RUNS_DIR) -> RunStore:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
        run_id = f"{stamp}-{_slug(client_name)}-{uuid.uuid4().hex[:4]}"
        store = cls(runs_dir / run_id)
        for sub in ("sources", "pages", "stages", "exports"):
            (store.dir / sub).mkdir(parents=True, exist_ok=True)
        store._write_json(store.dir / "run.json", {
            "run_id": run_id, "created_at": now_iso(), "status": "created", **_to_jsonable(meta),
        })
        return store

    @classmethod
    def open(cls, run_id: str, runs_dir: Path = DEFAULT_RUNS_DIR) -> RunStore:
        run_dir = runs_dir / run_id
        if not (run_dir / "run.json").exists():
            raise FileNotFoundError(f"No run {run_id} in {runs_dir}")
        return cls(run_dir)

    @property
    def run_id(self) -> str:
        return self.dir.name

    # Paths

    @property
    def sources_dir(self) -> Path:
        return self.dir / "sources"

    @property
    def pages_dir(self) -> Path:
        return self.dir / "pages"

    @property
    def exports_dir(self) -> Path:
        return self.dir / "exports"

    # run.json

    @property
    def meta(self) -> dict:
        return json.loads((self.dir / "run.json").read_text())

    def update_meta(self, **changes: object) -> None:
        with self._lock:
            meta = self.meta
            meta.update(_to_jsonable(changes))
            meta["updated_at"] = now_iso()
            self._write_json(self.dir / "run.json", meta)

    @property
    def status(self) -> RunStatus:
        return self.meta["status"]

    def set_status(self, status: RunStatus) -> None:
        self.update_meta(status=status)

    # Stage artifacts

    def save(self, name: str, obj: object) -> None:
        self._write_json(self.dir / "stages" / f"{name}.json", _to_jsonable(obj))

    def load(self, name: str, kind: type[T]) -> T:
        data = json.loads((self.dir / "stages" / f"{name}.json").read_text())
        return TypeAdapter(kind).validate_python(data)

    def has(self, name: str) -> bool:
        return (self.dir / "stages" / f"{name}.json").exists()

    # Logs

    def log_review(self, action: str, target: str, before: object = None, after: object = None,
                   actor: str = "consultant", reason: str = "") -> None:
        self.append_log("reviews.jsonl", {
            "at": now_iso(), "actor": actor, "action": action, "target": target,
            "before": _to_jsonable(before), "after": _to_jsonable(after), "reason": reason,
        })

    def log_llm_call(self, record: dict) -> None:
        self.append_log("llm_calls.jsonl", {"at": now_iso(), **_to_jsonable(record)})

    def read_log(self, name: str) -> list[dict]:
        path = self.dir / name
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    # Internals

    def append_log(self, name: str, record: dict) -> None:
        with self._lock, (self.dir / name).open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _write_json(path: Path, data: object) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp.replace(path)
