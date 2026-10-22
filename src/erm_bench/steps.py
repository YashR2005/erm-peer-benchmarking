"""The steps of a run, derived from what is on disk. Read-only: nothing here writes to a run.

Sources: runs/<id>/stages/*.json, llm_calls.jsonl, reviews.jsonl, exports/ and (for rewinds)
history/. A step is "done" when its files exist; "running" only when the API says a job is
running for the run. Anything that was never recorded (for example how long a download took)
is reported as not recorded rather than guessed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .spend import Budget
from .standard_setup import StandardSetup
from .store import RunStore

CLIENT = "client"
TRUNCATE = 2000

GROUPS = [("research", "Research"), ("scope", "Confirm scope"), ("analysis", "Analysis"),
          ("review", "Review findings"), ("export", "Export")]

# Review-log actions -> the step that holds the data they changed.
ACTION_HOME = {
    "select": "peers", "deselect": "peers", "set_peer_count": "peers", "set_bucket": "peers",
    "add_peer": "peers", "lock_scope": "scope",
    "remove_theme": "review", "restore_theme": "review", "rename_theme": "review",
    "recategorise_theme": "review", "reject_evidence": "review", "restore_evidence": "review",
    "add_remark": "review", "export": "export",
}


@dataclass
class Step:
    id: str
    label: str
    group: str
    files: list[str]
    depends_on: list[str]            # shown to the user; includes the scope gate
    invalidated_by: list[str]        # data dependencies: what makes this step stale
    llm_stages: list[str]            # llm_calls.jsonl `stage` values (a trailing * is a prefix)
    phase: str | None = None         # "research" or "analysis": which run status works on it
    company: str | None = None
    extra: dict = field(default_factory=dict)


class Disk:
    """One consistent read of a run folder."""

    def __init__(self, store: RunStore):
        self.store = store
        self.dir = store.dir
        self.meta = self._json(self.dir / "run.json") or {}
        self._stages: dict[str, object] = {}

    @staticmethod
    def _json(path: Path):
        try:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _lines(path: Path) -> list[dict]:
        out = []
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return out
        for line in text.splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        return out

    def has(self, name: str) -> bool:
        return (self.dir / "stages" / f"{name}.json").exists()

    def stage(self, name: str):
        if name not in self._stages:
            self._stages[name] = self._json(self.dir / "stages" / f"{name}.json")
        return self._stages[name]

    @property
    def calls(self) -> list[dict]:
        if not hasattr(self, "_calls"):
            self._calls = self._lines(self.dir / "llm_calls.jsonl")
        return self._calls

    @property
    def reviews(self) -> list[dict]:
        if not hasattr(self, "_reviews"):
            self._reviews = self._lines(self.dir / "reviews.jsonl")
        return self._reviews

    @property
    def exports(self) -> list[str]:
        d = self.dir / "exports"
        return sorted(f.name for f in d.glob("*.xlsx")) if d.exists() else []

    @property
    def status(self) -> str:
        return self.meta.get("status", "created")


# ---------------------------------------------------------------------------
# The graph


def company_name(disk: Disk, cid: str) -> str:
    if cid == CLIENT:
        profile = disk.stage("profile") or {}
        return profile.get("short_name") or (disk.meta.get("request") or {}).get("client_name", "Client")
    for c in (disk.stage("peers") or {}).get("candidates", []):
        if c["id"] == cid:
            return c["name"]
    return cid


def build_graph(disk: Disk) -> list[Step]:
    peers = disk.stage("peers") or {}
    candidates = peers.get("candidates", [])
    scope = disk.stage("scope")
    doc_companies = [CLIENT] + [c["id"] for c in candidates]
    if scope:
        extract_companies = [CLIENT] + list(scope.get("peer_ids", []))
    else:
        extract_companies = [CLIENT] + [c["id"] for c in candidates if c.get("selected")]
    # Companies that already have an extraction file stay visible after a rewind to scope.
    for cid in doc_companies:
        if cid not in extract_companies and disk.has(f"extract-{cid}"):
            extract_companies.append(cid)

    steps = [
        Step("request", "Request", "research", ["request.json"], [], [], ["request"]),
        Step("profile", f"Client profile: {company_name(disk, CLIENT)}", "research",
             ["profile.json"], ["request"], ["request"], ["profile", "profile_structure"],
             phase="research"),
        Step("peers", "Peer candidates", "research", ["peers.json"], ["profile"], ["profile"],
             ["peers", "peers_structure", "peer_lookup", "peer_lookup_structure"], phase="research"),
    ]
    for cid in doc_companies:
        steps.append(Step(
            f"documents:{cid}", f"Documents: {company_name(disk, cid)}", "research",
            ["documents.json"], ["profile"] if cid == CLIENT else ["peers"],
            ["profile"] if cid == CLIENT else ["peers"],
            [f"disclosure:{cid}", f"disclosure_structure:{cid}"], phase="research", company=cid))
    doc_ids = [f"documents:{cid}" for cid in doc_companies]
    steps.append(Step("scope", "Scope locked", "scope", ["scope.json"], ["peers", *doc_ids],
                      ["peers", *doc_ids], [], phase=None))
    for cid in extract_companies:
        steps.append(Step(
            f"extraction:{cid}", f"Extraction: {company_name(disk, cid)}", "analysis",
            [f"extract-{cid}.json"], ["scope", f"documents:{cid}"], [f"documents:{cid}"],
            [f"extract:{cid}"], phase="analysis", company=cid))
    ext_ids = [f"extraction:{cid}" for cid in extract_companies]
    steps.append(Step("context_risks", "Context risks (from the profile)", "analysis",
                      ["context_risks.json"], ["scope", "profile"], ["profile"], ["context_risks"],
                      phase="analysis"))
    steps.append(Step("themes", "Mapping to themes", "analysis", ["themes.json", "extractions.json"],
                      [*ext_ids, "context_risks"], [*ext_ids, "context_risks"], ["mapping"],
                      phase="analysis"))
    steps.append(Step("review", "Your review edits", "review", ["remarks.json"], ["themes"],
                      ["themes"], [], phase=None))
    steps.append(Step("export", "Excel workbook", "export", ["exports/*.xlsx"], ["review"],
                      ["review"], [], phase=None))
    return steps


def closure(steps: list[Step], step_id: str) -> list[str]:
    """The step and everything invalidated by it, in timeline order."""
    stale = {step_id}
    changed = True
    while changed:
        changed = False
        for s in steps:
            if s.id not in stale and any(d in stale for d in s.invalidated_by):
                stale.add(s.id)
                changed = True
    return [s.id for s in steps if s.id in stale]


# ---------------------------------------------------------------------------
# Status, calls, reviews


def _rewinds(disk: Disk) -> list[tuple[int, dict]]:
    return [(i, r) for i, r in enumerate(disk.reviews) if r.get("action") == "rewind"]


def split_calls(disk: Disk, step: Step) -> tuple[list[dict], list[dict]]:
    """(current calls, earlier calls from before a rewind that discarded this step)."""
    cutoff = 0
    for _, r in _rewinds(disk):
        after = r.get("after") or {}
        if step.id in after.get("discarded", []):
            cutoff = max(cutoff, int(after.get("llm_calls_before", 0)))

    def mine(call: dict) -> bool:
        stage = str(call.get("stage", ""))
        return any(stage == s or (s.endswith("*") and stage.startswith(s[:-1]))
                   for s in step.llm_stages)

    current, earlier = [], []
    for i, c in enumerate(disk.calls):
        if mine(c):
            (current if i >= cutoff else earlier).append({**c, "_index": i})
    return current, earlier


def step_reviews(disk: Disk, step: Step) -> list[dict]:
    """Review-log entries for this step that still apply (after the latest rewind that
    discarded the step they belong to)."""
    last_discard = -1
    for i, r in _rewinds(disk):
        if step.id in (r.get("after") or {}).get("discarded", []):
            last_discard = i
    out = []
    for i, r in enumerate(disk.reviews):
        if i <= last_discard or r.get("action") == "rewind":
            continue
        if review_home(r) == step.id:
            out.append(r)
    return out


def review_home(r: dict) -> str | None:
    action, target = r.get("action", ""), str(r.get("target", ""))
    if action in ("add_document", "mark_na", "restore_document"):
        parts = target.split("-")  # D-<company>-<n>
        return f"documents:{'-'.join(parts[1:-1])}" if len(parts) >= 3 else None
    return ACTION_HOME.get(action)


def _is_done(disk: Disk, step: Step) -> bool:
    if step.id.startswith("documents:"):
        docs = disk.stage("documents") or []
        return any(d.get("company_id") == step.company for d in docs)
    if step.id == "review":
        return disk.has("themes") and disk.has("remarks")
    if step.id == "export":
        return bool(disk.exports)
    return all(disk.has(Path(f).stem) for f in step.files)


def derive(disk: Disk, job: dict | None) -> dict[str, dict]:
    """step id -> {status, reason}."""
    steps = build_graph(disk)
    done = {s.id: _is_done(disk, s) for s in steps}
    status = disk.status
    running_job = bool(job and job.get("running"))
    out: dict[str, dict] = {}
    for s in steps:
        if done[s.id]:
            failed = s.id.startswith("documents:") and any(
                d.get("company_id") == s.company and d.get("title") == "(disclosure scan failed)"
                for d in disk.stage("documents") or [])
            out[s.id] = {"status": "failed" if failed else "done", "reason": ""}
            continue
        ready = all(done.get(d, False) for d in s.invalidated_by)
        if s.phase == "analysis":
            ready = ready and done["scope"]
        if s.id == "scope":
            ready = done["peers"] and all(done[d] for d in s.invalidated_by if d.startswith("documents:"))
        if not ready or s.phase is None:
            out[s.id] = {"status": "not started", "reason": ""}
            continue
        in_phase = (s.phase == "research" and status in ("created", "researching")) or \
                   (s.phase == "analysis" and status == "analysing")
        side_job = (s.phase == "research" and running_job and job.get("kind") in ("add_peer", "rescan")
                    and s.id.startswith("documents:"))
        if running_job and (in_phase or side_job):
            out[s.id] = {"status": "running", "reason": ""}
        elif job and job.get("error") and in_phase:
            out[s.id] = {"status": "failed", "reason": job["error"]}
        elif in_phase:
            why = "Stopped by you" if job and job.get("stopped") else "The run stopped before this step"
            out[s.id] = {"status": "stopped", "reason": why}
        else:
            out[s.id] = {"status": "not started", "reason": ""}
    return out


def money(x: float) -> float:
    return round(x, 4)


def money_label(millions: float | None) -> str:
    return f"S${round(millions)}M" if millions else "no market cap"


def call_numbers(setup: StandardSetup, calls: list[dict]) -> dict:
    usd = i = o = searches = 0
    seconds = 0.0
    for c in calls:
        b = Budget(setup, [c])
        usd += b.usd
        i += b.input_tokens
        o += b.output_tokens
        searches += b.searches
        seconds += c.get("seconds") or 0
    return {"calls": len(calls), "cost_usd": money(usd), "input_tokens": i, "output_tokens": o,
            "searches": searches, "seconds": round(seconds, 1)}


# ---------------------------------------------------------------------------
# Summaries and details


def _docs_of(disk: Disk, cid: str) -> list[dict]:
    return [d for d in disk.stage("documents") or [] if d.get("company_id") == cid]


def _summary(disk: Disk, step: Step, status: str) -> str:
    sid = step.id
    if status == "not started":
        return "Not started"
    if sid == "request":
        r = disk.stage("request") or {}
        year = r.get("reporting_year") or "latest"
        return f"{r.get('client_name', '')}, FY{year}, {r.get('peer_count', '?')} peers"
    if sid == "profile":
        p = disk.stage("profile")
        if not p:
            return "Researching the client" if status == "running" else "No profile saved"
        unsourced = sum(1 for f in p.get("facts", []) if not f.get("sources"))
        cap = p.get("market_cap_sgd_millions")
        return (f"{len(p.get('facts', []))} facts ({unsourced} unsourced); market cap "
                f"{money_label(cap)}; sector guide {p.get('sector_guide')}")
    if sid == "peers":
        pe = disk.stage("peers")
        if not pe:
            return "Scanning for peers" if status == "running" else "No peers saved"
        c = pe.get("candidates", [])
        return f"{len(c)} candidates, {sum(1 for x in c if x.get('selected'))} selected"
    if sid.startswith("documents:"):
        docs = _docs_of(disk, step.company)
        if not docs:
            return "Scanning for annual reports" if status == "running" else "No documents saved"
        ok = [d for d in docs if d.get("status") in ("ok", "uploaded")]
        best = ok[0] if ok else docs[0]
        return (f"{len(docs)} found; usable: {best.get('doc_type')} FY{best.get('fiscal_year')}"
                if ok else f"{len(docs)} found; none usable "
                           f"({best.get('status')}: {best.get('note') or 'no detail'})")
    if sid == "scope":
        s = disk.stage("scope")
        return f"Locked with {len(s.get('peer_ids', []))} peers" if s else "Not locked"
    if sid.startswith("extraction:"):
        e = disk.stage(f"extract-{step.company}")
        if not e:
            return "Reading the risk section" if status == "running" else "No extraction saved"
        risks = e.get("risks", [])
        v = sum(1 for r in risks if r.get("verification", {}).get("verified"))
        return f"{len(risks)} risks, {v} verified" if risks else e.get("note") or "No risks extracted"
    if sid == "context_risks":
        c = disk.stage("context_risks")
        return f"{len(c)} context risks" if c is not None else "Inferring context risks"
    if sid == "themes":
        t = disk.stage("themes")
        if t is None:
            return "Mapping risks to themes" if status == "running" else "No themes saved"
        return f"{len(t)} themes, {sum(1 for x in t if x.get('key') == 'unmapped')} 'Not mapped (review)'"
    if sid == "review":
        n = len(step_reviews(disk, step))
        return f"{n} edit(s) by you" if n else "No edits yet"
    if sid == "export":
        return ", ".join(disk.exports) or "Not exported"
    return ""


def list_steps(store: RunStore, setup: StandardSetup, job: dict | None = None) -> dict:
    disk = Disk(store)
    graph = build_graph(disk)
    st = derive(disk, job)
    rows = []
    for s in graph:
        cur, earlier = split_calls(disk, s)
        nums = call_numbers(setup, cur)
        times = [c["at"] for c in cur if c.get("at")]
        rows.append({
            "id": s.id, "label": s.label, "group": s.group, "status": st[s.id]["status"],
            "reason": st[s.id]["reason"], "summary": _summary(disk, s, st[s.id]["status"]),
            "depends_on": s.depends_on, "company": s.company,
            "seconds": nums["seconds"] if cur else None, "cost_usd": nums["cost_usd"] if cur else None,
            "finished_at": max(times) if times else None,
            "edits": len(step_reviews(disk, s)), "earlier_calls": len(earlier),
            "can_rewind": st[s.id]["status"] == "done" and s.id not in ("request", "review"),
        })
    running = [r["id"] for r in rows if r["status"] == "running"]
    live = running[0] if running else next(
        (r["id"] for r in rows if r["status"] in ("stopped", "failed")), None)
    if live is None:
        done = [r["id"] for r in rows if r["status"] == "done"]
        live = done[-1] if done else None
    groups = [{"key": k, "label": v} for k, v in GROUPS]
    return {"run_id": store.run_id, "status": disk.status, "groups": groups,
            "steps": rows, "live_step": live, "running": bool(running),
            "history": history_entries(store)}


def history_entries(store: RunStore) -> list[dict]:
    root = store.dir / "history"
    if not root.exists():
        return []
    out = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest = Disk._json(d / "manifest.json") or {}
        out.append({"folder": d.name, "step": manifest.get("step"), "at": manifest.get("at"),
                    "files": manifest.get("files", [])})
    return out


def _trim(value, full: bool) -> tuple[object, bool, int]:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if full or len(text) <= TRUNCATE:
        return value, False, len(text)
    return text[:TRUNCATE], True, len(text)


def _call_view(c: dict, setup: StandardSetup, full: bool) -> dict:
    out, truncated, chars = _trim(c.get("output"), full)
    nums = call_numbers(setup, [c])
    seconds = c.get("seconds")
    return {"stage": c.get("stage"), "prompt": c.get("prompt"), "model": c.get("model"),
            "effort": c.get("effort"), "finished_at": c.get("at"), "seconds": seconds,
            "cost_usd": nums["cost_usd"], "input_tokens": nums["input_tokens"],
            "output_tokens": nums["output_tokens"], "searches": nums["searches"],
            "stop_reason": c.get("stop_reason"), "index": c.get("_index"),
            "output": out, "output_truncated": truncated, "output_chars": chars}


def step_detail(store: RunStore, setup: StandardSetup, step_id: str, job: dict | None = None,
                full: bool = False, raw: bool = False) -> dict | None:
    disk = Disk(store)
    graph = build_graph(disk)
    step = next((s for s in graph if s.id == step_id), None)
    if step is None:
        return None
    st = derive(disk, job)[step.id]
    cur, earlier = split_calls(disk, step)
    nums = call_numbers(setup, cur)
    times = [c["at"] for c in cur if c.get("at")]
    warnings = _warnings(disk, step, cur, st)
    detail = {
        "id": step.id, "label": step.label, "group": step.group, "status": st["status"],
        "reason": st["reason"], "summary": _summary(disk, step, st["status"]),
        "depends_on": step.depends_on, "files": step.files, "company": step.company,
        "inputs": _inputs(disk, step, cur),
        "output": _output(disk, step),
        "time": {"seconds_in_model_calls": nums["seconds"] if cur else None,
                 "finished_at": max(times) if times else None,
                 "note": ("Model-call time only. Download and page-check time was not recorded."
                          if step.id.startswith(("documents:", "extraction:")) else
                          None if cur else "Not recorded for this step.")},
        "cost": {**nums, "earlier_calls": call_numbers(setup, earlier) if earlier else None},
        "calls": [_call_view(c, setup, full) for c in cur],
        "earlier_calls": [_call_view(c, setup, False) for c in earlier],
        "warnings": warnings,
        "actions": [{"at": r.get("at"), "action": r.get("action"), "target": r.get("target"),
                     "before": r.get("before"), "after": r.get("after"), "reason": r.get("reason", ""),
                     "actor": r.get("actor", "consultant")} for r in step_reviews(disk, step)],
        "history": [h for h in history_entries(store) if h["step"] == step.id],
    }
    if raw:
        detail["raw"] = {Path(f).stem: disk.stage(Path(f).stem) for f in step.files
                         if f.endswith(".json") and disk.has(Path(f).stem)}
    return detail


def _warnings(disk: Disk, step: Step, calls: list[dict], st: dict) -> list[str]:
    w = []
    if st["status"] in ("failed", "stopped") and st["reason"]:
        w.append(st["reason"])
    for c in calls:
        if c.get("stop_reason") not in (None, "end_turn"):
            w.append(f"{c.get('stage')}: model stop reason '{c.get('stop_reason')}'")
    sid = step.id
    if sid == "profile":
        w += (disk.stage("profile") or {}).get("warnings", [])
    elif sid == "peers":
        pe = disk.stage("peers") or {}
        if pe.get("scan_notes"):
            w.append(pe["scan_notes"])
        for c in pe.get("candidates", []):
            w += [f"{c['name']}: {x}" for x in c.get("warnings", [])]
    elif sid.startswith("documents:"):
        for d in _docs_of(disk, step.company):
            if d.get("note") and d.get("status") != "found":
                w.append(f"{d['id']} ({d.get('status')}): {d['note']}")
    elif sid.startswith("extraction:"):
        e = disk.stage(f"extract-{step.company}") or {}
        bad = [r["id"] for r in e.get("risks", []) if not r.get("verification", {}).get("verified")]
        if bad:
            w.append(f"{len(bad)} quote(s) not verified against the PDF: {', '.join(bad)}")
        if e and not e.get("risks"):
            w.append(e.get("note") or "No risks extracted")
    elif sid == "context_risks":
        for c in disk.stage("context_risks") or []:
            w += [f"{c['id']}: {x}" for x in c.get("warnings", [])]
    elif sid == "themes":
        n = sum(1 for t in disk.stage("themes") or [] if t.get("key") == "unmapped")
        if n:
            w.append("Some risks were not mapped to a theme and sit under 'Not mapped (review)'.")
    return w


def _prompt_info(calls: list[dict]) -> list[dict]:
    seen, out = set(), []
    for c in calls:
        key = (c.get("prompt"), c.get("model"), c.get("effort"))
        if key not in seen:
            seen.add(key)
            out.append({"label": f"Prompt {c.get('prompt')}",
                        "value": f"model {c.get('model')}, effort {c.get('effort')}"})
    return out


def _inputs(disk: Disk, step: Step, calls: list[dict]) -> list[dict]:
    sid = step.id
    rows: list[dict] = []
    req = disk.stage("request") or {}
    profile = disk.stage("profile") or {}
    if sid == "request":
        rows = [{"label": k.replace("_", " ").capitalize(), "value": v}
                for k, v in req.items() if v not in (None, "", [])]
    elif sid == "profile":
        rows = [{"label": "Client", "value": req.get("client_name")},
                {"label": "Reporting year", "value": req.get("reporting_year") or "latest available"},
                {"label": "Sector (given)", "value": req.get("sector") or "not given"}]
    elif sid == "peers":
        rows = [{"label": "Client profile used", "value": f"{profile.get('legal_name')} "
                 f"({profile.get('sector')}, guide {profile.get('sector_guide')}, "
                 f"{money_label(profile.get('market_cap_sgd_millions'))})"},
                {"label": "Peers wanted", "value": req.get("peer_count")},
                {"label": "Market-cap bucket", "value": req.get("market_cap_bucket") or "none"},
                {"label": "Include / exclude",
                 "value": f"{req.get('include') or []} / {req.get('exclude') or []}"}]
    elif sid.startswith("documents:"):
        wanted = req.get("reporting_year") or profile.get("latest_annual_report_year")
        rows = [{"label": "Company", "value": company_name(disk, step.company)},
                {"label": "Wanted fiscal year", "value": wanted or "latest"}]
    elif sid == "scope":
        peers = (disk.stage("peers") or {}).get("candidates", [])
        rows = [{"label": "Candidates", "value": ", ".join(
            f"{c['name']}{' (selected)' if c.get('selected') else ''}" for c in peers) or "none"}]
    elif sid.startswith("extraction:"):
        scope = disk.stage("scope") or {}
        doc_id = (scope.get("documents") or {}).get(step.company)
        e = disk.stage(f"extract-{step.company}") or {}
        doc_id = doc_id or e.get("doc_id")
        doc = next((d for d in disk.stage("documents") or [] if d.get("id") == doc_id), None)
        if doc:
            rows = [{"label": "Document", "value": f"{doc.get('title')} ({doc['id']}), "
                                                    f"FY{doc.get('fiscal_year')}, "
                                                    f"{doc.get('page_count')} pages"},
                    {"label": "Pages read",
                     "value": ", ".join(map(str, doc.get("risk_pages", []))) or "none located"},
                    {"label": "Source", "value": doc.get("url") or "uploaded"}]
        else:
            rows = [{"label": "Document", "value": "none chosen: insufficient evidence"}]
    elif sid == "context_risks":
        facts = profile.get("facts", [])
        rows = [{"label": "Profile facts used", "value": f"{len(facts)} (" +
                 ", ".join(f["id"] for f in facts[:12]) + (", ..." if len(facts) > 12 else "") + ")"},
                {"label": "Sector guide", "value": profile.get("sector_guide")}]
    elif sid == "themes":
        ex = disk.stage("extractions") or []
        rows = [{"label": "Disclosed risks mapped", "value": sum(len(e.get("risks", [])) for e in ex)},
                {"label": "Company categories mapped",
                 "value": sum(len(e.get("categories", [])) for e in ex)},
                {"label": "Context risks mapped", "value": len(disk.stage("context_risks") or [])}]
    elif sid == "export":
        rows = [{"label": "Exports logged", "value": len(disk.exports)}]
    return [r for r in rows if r["value"] is not None] + _prompt_info(calls)


def _output(disk: Disk, step: Step) -> dict | None:
    sid = step.id
    if sid == "request":
        return {"kind": "fields", "fields": disk.stage("request") or {}}
    if sid == "profile":
        p = disk.stage("profile")
        return None if not p else {"kind": "profile", "facts": p.get("facts", []), "fields": {
            k: p.get(k) for k in ("legal_name", "listed", "exchange", "ticker", "market_cap_sgd_millions",
                                   "market_cap_as_of", "bucket", "sector", "sector_guide", "segments",
                                   "countries", "total_assets", "revenue", "employees", "parent",
                                   "related_listed_entities", "latest_annual_report_year")}}
    if sid == "peers":
        p = disk.stage("peers")
        return None if not p else {"kind": "peers", "scan_notes": p.get("scan_notes"),
                                   "candidates": p.get("candidates", [])}
    if sid.startswith("documents:"):
        docs = _docs_of(disk, step.company)
        return {"kind": "documents", "documents": docs} if docs else None
    if sid == "scope":
        s = disk.stage("scope")
        return None if not s else {"kind": "scope", "scope": s}
    if sid.startswith("extraction:"):
        e = disk.stage(f"extract-{step.company}")
        if not e:
            return None
        docs = {d["id"]: d for d in disk.stage("documents") or []}
        doc = docs.get(e.get("doc_id"))
        return {"kind": "extraction", "note": e.get("note"), "doc_url": doc.get("url") if doc else None,
                "doc_title": doc.get("title") if doc else None,
                "has_risk_disclosure": e.get("has_risk_disclosure"),
                "category_structure_note": e.get("category_structure_note"),
                "categories": [{"name": k["name"], "sub_categories": k.get("sub_categories", []),
                                "quote": k.get("quote"), "page": k.get("page"),
                                "verified": k.get("verification", {}).get("verified")}
                               for k in e.get("categories", [])],
                "risks": [{"id": r["id"], "title": r["title"], "summary": r.get("summary"),
                           "company_category": r.get("company_category"), "quote": r.get("quote"),
                           "page": r.get("page"), "verified": r.get("verification", {}).get("verified"),
                           "score": round(r.get("verification", {}).get("score") or 0),
                           "note": r.get("verification", {}).get("note"),
                           "url": doc.get("url") if doc else None}
                          for r in e.get("risks", [])]}
    if sid == "context_risks":
        c = disk.stage("context_risks")
        facts = {f["id"]: f["text"] for f in (disk.stage("profile") or {}).get("facts", [])}
        return None if c is None else {"kind": "context_risks", "risks": [
            {**r, "driver_facts": [{"id": f, "text": facts.get(f, "")} for f in r.get("driver_fact_ids", [])]}
            for r in c]}
    if sid == "themes":
        t = disk.stage("themes")
        if t is None:
            return None
        counts: dict[str, int] = {}
        for e in disk.stage("extractions") or []:
            for r in e.get("risks", []):
                counts[r.get("theme_key")] = counts.get(r.get("theme_key"), 0) + 1
        return {"kind": "themes", "themes": [{**x, "risks": counts.get(x["key"], 0)} for x in t]}
    if sid == "review":
        return {"kind": "review", "remarks": disk.stage("remarks") or []}
    if sid == "export":
        return {"kind": "export", "files": disk.exports} if disk.exports else None
    return None
