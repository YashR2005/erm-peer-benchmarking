"""HTTP API for the web frontend.

Long stages (research, adding a peer, a bucket re-scan, analysis, chat
instructions) run as background jobs, one at a time per run. The frontend
polls GET /api/runs/{id} for the run state and the job's live progress.

  uv run erm-bench serve            API on :8000, plus frontend/dist if built
"""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .chat import ChatSession
from .llm import LLMError
from .pipeline import (
    CLIENT,
    LLMFactory,
    Pipeline,
    ScopeError,
    make_llm,
    parse_request,
    request_from_draft,
    short_exchange,
)
from .product_a import GAP_LABELS
from .schemas import BucketKey, Request
from .spend import Budget
from .standard_setup import REPO_ROOT, StandardSetup
from .store import DEFAULT_RUNS_DIR, RunStore, now_iso
from .timing import StopRequested

FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
MAX_UPLOAD_MB = 80


# ---------------------------------------------------------------------------
# Background jobs


@dataclass
class Job:
    kind: str
    started_at: str = field(default_factory=now_iso)
    progress: list[dict] = field(default_factory=list)
    error: str | None = None
    finished_at: str | None = None
    stop: threading.Event = field(default_factory=threading.Event)   # the Stop button
    stopped: bool = False

    def add(self, message: str) -> None:
        self.progress.append({"at": now_iso(), "message": message})

    @property
    def running(self) -> bool:
        return self.finished_at is None

    def to_dict(self) -> dict:
        return {"kind": self.kind, "running": self.running, "started_at": self.started_at,
                "finished_at": self.finished_at, "progress": self.progress, "error": self.error,
                "stopping": self.running and self.stop.is_set(), "stopped": self.stopped}


class Jobs:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def get(self, run_id: str) -> Job | None:
        return self._jobs.get(run_id)

    def busy(self, run_id: str) -> bool:
        job = self._jobs.get(run_id)
        return bool(job and job.running)

    def start(self, run_id: str, kind: str, work: Callable[[Job], None]) -> Job:
        with self._lock:
            if self.busy(run_id):
                raise HTTPException(409, "This run is still working. Wait for it to finish.")
            job = Job(kind)
            self._jobs[run_id] = job

        def target() -> None:
            try:
                work(job)
            except StopRequested:
                job.stopped = True
                job.add("Stopped by you")
            except ScopeError as e:
                job.error = str(e)
            except Exception as e:  # report every failure to the UI, not just the log
                traceback.print_exc()
                job.error = f"{type(e).__name__}: {e}"
            finally:
                job.finished_at = now_iso()

        threading.Thread(target=target, daemon=True, name=f"{kind}:{run_id}").start()
        return job

    def stop(self, run_id: str) -> bool:
        """Ask the running job to stop at its next check. False if nothing is running."""
        job = self._jobs.get(run_id)
        if not (job and job.running):
            return False
        job.stop.set()
        return True


# ---------------------------------------------------------------------------
# Request bodies


class NewRun(BaseModel):
    client_name: str
    reporting_year: int | None = None
    peer_count: int | None = None
    market_cap_bucket: BucketKey | None = None
    sector: str | None = None
    focus_regions: list[str] = []
    notes: str = ""


class ChatMessage(BaseModel):
    message: str


class Selection(BaseModel):
    selected: bool


class PeerName(BaseModel):
    name: str


class BucketChoice(BaseModel):
    bucket: BucketKey | None = None


class PeerCount(BaseModel):
    count: int


class DocumentUrl(BaseModel):
    company_id: str
    url: str


class Reason(BaseModel):
    reason: str = "Marked not available by the consultant"


class ThemeChange(BaseModel):
    removed: bool | None = None
    name: str | None = None
    category: str | None = None


class EvidenceChange(BaseModel):
    rejected: bool


class Remark(BaseModel):
    text: str


# ---------------------------------------------------------------------------
# State as JSON


def _findings_state(p: Pipeline) -> dict:
    f = p.findings()
    setup = p.setup
    docs = {d.id: d for d in p.documents}
    theme_view = lambda t: {"key": t.key, "name": t.name, "category": t.category,  # noqa: E731
                            "category_name": setup.taxonomy_name(t.category),
                            "description": t.description, "removed": t.removed}
    risks = []
    for r in f.risks:
        doc = docs.get(r.doc_id)
        risks.append({
            "id": r.id, "company_id": r.company_id, "theme_key": r.theme_key, "title": r.title,
            "summary": r.summary, "company_category": r.company_category, "quote": r.quote,
            "page": r.page, "doc_id": r.doc_id, "doc_title": doc.title if doc else None,
            "url": doc.url if doc else None, "verified": r.verification.verified,
            "score": round(r.verification.score), "note": r.verification.note,
            "rejected": r.rejected, "counts": r.counts,
        })
    facts = {fact.id: fact.text for fact in p.profile.facts}
    verified = sum(1 for r in f.risks if r.verification.verified)
    return {
        "companies": [{"id": c.id, "name": c.name} for c in f.companies],
        "client_disclosed": f.client_disclosed,
        "stats": {
            "risks": len(f.risks), "verified": verified, "unverified": len(f.risks) - verified,
            "rejected": sum(1 for r in f.risks if r.rejected), "themes": len(f.rows),
            "context_risks": len(f.context_risks), "gaps": len(f.gaps),
        },
        "rows": [{"theme": theme_view(row.theme), "context_risk_ids": row.context_risk_ids,
                  "cells": {cid: [r.id for r in rs] for cid, rs in row.cells.items()}}
                 for row in f.rows],
        "removed_themes": [theme_view(t) for t in f.themes if t.removed],
        "gaps": [{"type": g.type, "label": GAP_LABELS[g.type], "theme_key": g.theme.key,
                  "theme_name": g.theme.name, "category": setup.taxonomy_name(g.theme.category),
                  "detail": g.detail} for g in f.gaps],
        "structure": {
            "columns": [{
                "company_id": col.company.id, "has_structure": col.has_structure, "note": col.note,
                "categories": [{"id": k.id, "name": k.name, "sub_categories": k.sub_categories,
                                "taxonomy_key": k.taxonomy_key, "mapping_note": k.mapping_note,
                                "quote": k.quote, "page": k.page} for k in col.categories],
            } for col in f.structure.columns],
            "by_taxonomy": f.structure.by_taxonomy,
            "observations": f.structure.observations,
        },
        "risks": risks,
        "context_risks": [{**c.model_dump(mode="json"),
                           "driver_facts": [{"id": fid, "text": facts.get(fid, "")}
                                            for fid in c.driver_fact_ids]}
                          for c in f.context_risks],
        "extractions": [{"company_id": e.company_id, "doc_id": e.doc_id, "note": e.note,
                         "has_risk_disclosure": e.has_risk_disclosure} for e in f.extractions],
        "remarks": f.remarks,
    }


def run_state(p: Pipeline, job: Job | None) -> dict:
    store = p.store
    meta = store.meta
    state: dict = {
        "run": {k: meta.get(k) for k in ("run_id", "status", "created_at", "updated_at", "method",
                                         "setup_version", "provider", "model")},
        "request": meta.get("request"),
        "job": job.to_dict() if job else None,
        "chat": store.read_log("chat.jsonl"),
        "exports": sorted(f.name for f in store.exports_dir.glob("*.xlsx")),
        "spend": Budget(p.setup, store.read_log("llm_calls.jsonl")).summary(),
        "profile": None, "peers": None, "documents": [], "companies": {}, "scope": None,
        "findings": None,
    }
    if store.has("profile"):
        state["profile"] = p.profile.model_dump(mode="json")
    if store.has("peers"):
        state["peers"] = p.peers.model_dump(mode="json")
    if store.has("documents"):
        state["documents"] = [d.model_dump(mode="json") for d in p.documents]
        ids = [CLIENT] + [c.id for c in p.peers.candidates] if store.has("peers") else [CLIENT]
        for cid in ids:
            doc = p.primary_document(cid)
            state["companies"][cid] = {"availability": p.availability(cid),
                                       "primary_doc_id": doc.id if doc else None}
    if store.has("scope"):
        state["scope"] = p.scope
    if meta["status"] in ("awaiting_findings", "exported") and store.has("extractions"):
        state["findings"] = _findings_state(p)
    return state


# ---------------------------------------------------------------------------
# App


def create_app(setup: StandardSetup | None = None, runs_dir: Path = DEFAULT_RUNS_DIR,
               llm_factory: LLMFactory = None, fetch=None,
               static_dir: Path | None = FRONTEND_DIST) -> FastAPI:
    setup = setup or StandardSetup.load()
    jobs = Jobs()
    # Reading setup.model here fails at startup if the configured model isn't allowed.
    app = FastAPI(title="ERM Peer Benchmarking", description=f"Model: {setup.model}",
                  docs_url="/api/docs",
                  openapi_url="/api/openapi.json", redoc_url=None)
    api = APIRouter(prefix="/api")

    def pipeline(run_id: str, progress: Callable[[str], None] | None = None,
                 with_llm: bool = True, stop: threading.Event | None = None) -> Pipeline:
        try:
            if not with_llm:  # reading state needs no model client (and no API key)
                store = RunStore.open(run_id, runs_dir=runs_dir)
                return Pipeline(store, StandardSetup.load(store.meta["setup_version"]), llm=None,
                                fetch=fetch)
            return Pipeline.resume(run_id, llm_factory=llm_factory, runs_dir=runs_dir,
                                   fetch=fetch, progress=progress, stop=stop)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        except LLMError as e:
            raise HTTPException(503, str(e)) from e

    def state(run_id: str) -> dict:
        return run_state(pipeline(run_id, with_llm=False), jobs.get(run_id))

    def idle(run_id: str) -> None:
        if jobs.busy(run_id):
            raise HTTPException(409, "This run is still working. Wait for it to finish.")

    def act(run_id: str, action: Callable[[Pipeline], object]) -> dict:
        """A quick checkpoint action that needs no model call, then the new state."""
        idle(run_id)
        try:
            action(pipeline(run_id, with_llm=False))
        except ScopeError as e:
            raise HTTPException(400, str(e)) from e
        return state(run_id)

    def background(run_id: str, kind: str, work: Callable[[Pipeline], object]) -> dict:
        jobs.start(run_id, kind,
                   lambda job: work(pipeline(run_id, progress=job.add, stop=job.stop)))
        return state(run_id)

    def log_chat(run_id: str, role: str, text: str) -> None:
        RunStore.open(run_id, runs_dir=runs_dir).append_log(
            "chat.jsonl", {"at": now_iso(), "role": role, "text": text})

    # -- Setup and runs ----------------------------------------------------------------

    @api.get("/health")
    def health() -> dict:
        return {"ok": True, "provider": setup.provider, "model": setup.model}

    @api.get("/setup")
    def get_setup() -> dict:
        return {
            "name": setup.name, "version": setup.version, "provider": setup.provider,
            "model": setup.model, "bucket_currency": setup.bucket_currency,
            "bucket_status": setup.method["market_cap_buckets"].get("status"),
            "buckets": [{"key": b.key, "label": b.label, "min": b.min, "max": b.max,
                         "description": b.describe(setup.bucket_currency)} for b in setup.buckets],
            "taxonomy": setup.taxonomy,
            "default_peer_count": setup.method["peers"]["default_count"],
            "time_limits": setup.method.get("time_limits", {}),
            "verify": setup.method["verify"],
        }

    @api.get("/runs")
    def list_runs() -> list[dict]:
        runs = []
        if runs_dir.exists():
            for run_json in runs_dir.glob("*/run.json"):
                store = RunStore(run_json.parent)
                meta = store.meta
                profile = store.load("profile", dict) if store.has("profile") else {}
                peers = store.load("peers", dict) if store.has("peers") else {}
                runs.append({
                    "run_id": meta["run_id"], "status": meta["status"],
                    "created_at": meta.get("created_at"), "updated_at": meta.get("updated_at"),
                    "client": profile.get("short_name") or meta.get("request", {}).get("client_name"),
                    "legal_name": profile.get("legal_name"),
                    "listing": (f"{short_exchange(profile.get('exchange'))}: {profile.get('ticker')}"
                                if profile.get("listed") else None),
                    "reporting_year": meta.get("request", {}).get("reporting_year"),
                    "peers": [c["name"] for c in peers.get("candidates", []) if c.get("selected")],
                    "model": meta.get("model"), "busy": jobs.busy(meta["run_id"]),
                    "usd": round(Budget(setup, store.read_log("llm_calls.jsonl")).usd, 2),
                })
        return sorted(runs, key=lambda r: r["created_at"] or "", reverse=True)

    @api.post("/runs")
    def create_run(body: NewRun) -> dict:
        if not body.client_name.strip():
            raise HTTPException(400, "Enter the client's name.")
        request = Request(**{**body.model_dump(exclude={"peer_count"}),
                             "client_name": body.client_name.strip(),
                             "peer_count": body.peer_count or setup.method["peers"]["default_count"]})
        try:
            p = Pipeline.start(request, setup, llm_factory=llm_factory, runs_dir=runs_dir, fetch=fetch)
        except LLMError as e:
            raise HTTPException(503, str(e)) from e
        return background(p.store.run_id, "research", lambda q: q.research())

    @api.post("/chat")
    def start_by_chat(body: ChatMessage) -> dict:
        """Start a run from a sentence, e.g. 'Benchmark City Developments, large caps only'."""
        try:
            draft = parse_request(body.message, (llm_factory or make_llm)(setup, None), setup)
        except LLMError as e:
            raise HTTPException(503, str(e)) from e
        if not draft.client_name:
            return {"run_id": None, "reply": "Which company should I benchmark? Only the name is "
                                             "required."}
        request = request_from_draft(draft, setup)
        p = Pipeline.start(request, setup, llm_factory=llm_factory, runs_dir=runs_dir, fetch=fetch)
        run_id = p.store.run_id
        log_chat(run_id, "user", body.message)
        year = f" for FY{request.reporting_year}" if request.reporting_year else ""
        bucket = f", {request.market_cap_bucket} caps only" if request.market_cap_bucket else ""
        log_chat(run_id, "assistant", f"Benchmarking {request.client_name}{year}: "
                                      f"{request.peer_count} listed peers{bucket}.")
        background(run_id, "research", lambda q: q.research())
        return {"run_id": run_id, "reply": None}

    @api.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        return state(run_id)

    @api.post("/runs/{run_id}/resume")
    def resume(run_id: str) -> dict:
        p = pipeline(run_id, with_llm=False)
        status = p.store.status
        if status in ("created", "researching"):
            return background(run_id, "research", lambda q: q.research())
        if status == "analysing":
            return background(run_id, "analysis", lambda q: q.analyse())
        raise HTTPException(400, f"Nothing to resume: the run is '{status}'.")

    @api.post("/runs/{run_id}/stop")
    def stop(run_id: str) -> dict:
        """Stop the running job. It ends at its next check; what finished is saved."""
        if not jobs.stop(run_id):
            raise HTTPException(400, "Nothing is running.")
        return state(run_id)

    @api.post("/runs/{run_id}/chat")
    def chat(run_id: str, body: ChatMessage) -> dict:
        idle(run_id)
        log_chat(run_id, "user", body.message)

        def work(job: Job) -> None:
            session = ChatSession(setup, runs_dir=runs_dir, llm_factory=llm_factory, fetch=fetch,
                                  views=False)
            session.pipeline = pipeline(run_id, progress=job.add, stop=job.stop)
            try:
                replies = session.handle(body.message, progress=job.add)
            except StopRequested:
                log_chat(run_id, "assistant", "Stopped. What finished is saved.")
                raise
            except Exception as e:
                log_chat(run_id, "assistant", f"Something failed: {type(e).__name__}: {e}")
                raise
            for reply in replies:
                log_chat(run_id, "assistant", reply.text)

        jobs.start(run_id, "chat", work)
        return state(run_id)

    # -- Checkpoint 1 ------------------------------------------------------------------

    @api.post("/runs/{run_id}/peers/{peer_id}")
    def select_peer(run_id: str, peer_id: str, body: Selection) -> dict:
        return act(run_id, lambda p: p.select([peer_id], selected=body.selected))

    @api.post("/runs/{run_id}/peers")
    def add_peer(run_id: str, body: PeerName) -> dict:
        if not body.name.strip():
            raise HTTPException(400, "Enter a company name.")
        return background(run_id, "add_peer", lambda p: p.add_peer(body.name.strip()))

    @api.post("/runs/{run_id}/bucket")
    def set_bucket(run_id: str, body: BucketChoice) -> dict:
        return background(run_id, "rescan", lambda p: p.set_bucket(body.bucket))

    @api.post("/runs/{run_id}/peer-count")
    def set_peer_count(run_id: str, body: PeerCount) -> dict:
        if not 1 <= body.count <= 10:
            raise HTTPException(400, "Choose between 1 and 10 peers.")
        return act(run_id, lambda p: p.set_peer_count(body.count))

    @api.post("/runs/{run_id}/documents")
    def add_document_url(run_id: str, body: DocumentUrl) -> dict:
        return act(run_id, lambda p: p.add_document(body.company_id, url=body.url.strip()))

    @api.post("/runs/{run_id}/documents/upload")
    def upload_document(run_id: str, company_id: Annotated[str, Form()],
                        file: Annotated[UploadFile, File()]) -> dict:
        data = file.file.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
        if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"The file is larger than {MAX_UPLOAD_MB} MB.")
        return act(run_id, lambda p: p.add_document(company_id, data=data,
                                                    title=file.filename or "Uploaded PDF"))

    @api.post("/runs/{run_id}/documents/{doc_id}/na")
    def mark_na(run_id: str, doc_id: str, body: Reason) -> dict:
        return act(run_id, lambda p: p.mark_na(doc_id, body.reason))

    @api.post("/runs/{run_id}/documents/{doc_id}/restore")
    def restore_document(run_id: str, doc_id: str) -> dict:
        """Undo "Mark n/a"."""
        return act(run_id, lambda p: p.restore_document(doc_id))

    @api.post("/runs/{run_id}/lock")
    def lock(run_id: str) -> dict:
        idle(run_id)
        try:
            pipeline(run_id, with_llm=False).lock_scope()
        except ScopeError as e:
            raise HTTPException(400, str(e)) from e
        return background(run_id, "analysis", lambda p: p.analyse())

    # -- Checkpoint 2 ------------------------------------------------------------------

    @api.patch("/runs/{run_id}/themes/{key}")
    def change_theme(run_id: str, key: str, body: ThemeChange) -> dict:
        def apply(p: Pipeline) -> None:
            if body.removed is True:
                p.remove_themes([key])
            elif body.removed is False:
                p.restore_themes([key])
            if body.name:
                p.rename_theme(key, body.name.strip())
            if body.category:
                p.recategorise_theme(key, body.category)
        return act(run_id, apply)

    @api.post("/runs/{run_id}/evidence/{risk_id}")
    def change_evidence(run_id: str, risk_id: str, body: EvidenceChange) -> dict:
        return act(run_id, lambda p: p.reject_evidence([risk_id], rejected=body.rejected))

    @api.post("/runs/{run_id}/remarks")
    def add_remark(run_id: str, body: Remark) -> dict:
        if not body.text.strip():
            raise HTTPException(400, "Write a remark first.")
        return act(run_id, lambda p: p.add_remark(body.text.strip()))

    @api.post("/runs/{run_id}/export")
    def export(run_id: str) -> dict:
        idle(run_id)
        try:
            path = pipeline(run_id, with_llm=False).export()
        except ScopeError as e:
            raise HTTPException(400, str(e)) from e
        return {"file": path.name, "url": f"/api/runs/{run_id}/exports/{path.name}",
                "state": state(run_id)}

    @api.get("/runs/{run_id}/exports/{name}")
    def download(run_id: str, name: str) -> FileResponse:
        path = pipeline(run_id, with_llm=False).store.exports_dir / name
        if Path(name).name != name or not path.is_file():
            raise HTTPException(404, "No such export.")
        return FileResponse(path, filename=name, media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))

    app.include_router(api)
    if static_dir and static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
    return app
