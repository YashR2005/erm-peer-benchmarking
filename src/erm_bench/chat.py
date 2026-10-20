"""Chat over the fixed stages (phase 1 demo).

Every message is turned into an action on the pipeline: the chat can fill in
inputs and make checkpoint decisions, but it can't skip a stage, change their
order or change the method.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from .pipeline import (
    CLIENT,
    LLMFactory,
    Pipeline,
    ScopeError,
    make_llm,
    parse_request,
    request_from_draft,
)
from .schemas import FindingsCommands, ScopeCommands
from .standard_setup import StandardSetup
from .store import DEFAULT_RUNS_DIR
from .views import (
    Reply,
    evidence_reply,
    findings_replies,
    peers_reply,
    scope_replies,
    sources_reply,
)

Progress = Callable[[str], None]

GO = re.compile(r"^\s*(go|lock|lock it|lock scope|run|run it|proceed|go ahead|looks good)\W*$", re.I)
EXPORT = re.compile(r"^\s*(export|download|excel)\W*$", re.I)

WELCOME = (
    "Hi. Tell me which company to benchmark and I'll build its risk profile benchmark "
    "(Product A). For example: *Benchmark City Developments for FY2025, 3 peers, large caps "
    "only*. Only the company name is required."
)


class ChatSession:
    def __init__(self, setup: StandardSetup | None = None, runs_dir: Path = DEFAULT_RUNS_DIR,
                 llm_factory: LLMFactory = None, fetch=None, views: bool = True):
        self.setup = setup or StandardSetup.load()
        self.runs_dir = runs_dir
        self.llm_factory = llm_factory or make_llm
        self.fetch = fetch
        # views=False: return only the short replies; the caller renders the run state itself.
        self.views = views
        self.pipeline: Pipeline | None = None

    def _view(self, replies: list[Reply]) -> list[Reply]:
        return replies if self.views else []

    @property
    def status(self) -> str:
        return self.pipeline.store.status if self.pipeline else "request"

    def resume(self, run_id: str) -> list[Reply]:
        self.pipeline = Pipeline.resume(run_id, llm_factory=self.llm_factory,
                                        runs_dir=self.runs_dir, fetch=self.fetch)
        return self.current_view()

    def current_view(self) -> list[Reply]:
        status = self.status
        if status == "awaiting_scope":
            return scope_replies(self.pipeline)
        if status in ("awaiting_findings", "exported"):
            return findings_replies(self.pipeline)
        if status in ("researching", "analysing", "created"):
            return [Reply(f"This run stopped while {status}. Say **resume** to run that stage "
                          "again.")]
        return [Reply(WELCOME)]

    def handle(self, message: str, progress: Progress | None = None) -> list[Reply]:
        progress = progress or (lambda m: None)
        try:
            if self.pipeline is None:
                return self._start(message, progress)
            self.pipeline.progress = progress
            status = self.status
            if status in ("created", "researching", "analysing"):
                return self._resume_stage(message, progress)
            if status == "awaiting_scope":
                return self._scope(message, progress)
            if status in ("awaiting_findings", "exported"):
                return self._findings(message)
            return [Reply(f"Unexpected run status '{status}'.")]
        except ScopeError as e:
            return [Reply(f"⚠️ {e}")]

    # Stage 1 · Request -> stage 2 · Research

    def _start(self, message: str, progress: Progress) -> list[Reply]:
        draft = parse_request(message, self.llm_factory(self.setup, None), self.setup)
        if not draft.client_name:
            return [Reply("Which company should I benchmark? Only the name is required.")]
        request = request_from_draft(draft, self.setup)
        self.pipeline = Pipeline.start(request, self.setup, llm_factory=self.llm_factory,
                                       runs_dir=self.runs_dir, fetch=self.fetch, progress=progress)
        bucket = (f", {request.market_cap_bucket} caps only" if request.market_cap_bucket else "")
        year = f" for FY{request.reporting_year}" if request.reporting_year else ""
        intro = Reply(f"Benchmarking **{request.client_name}**{year}: {request.peer_count} listed "
                      f"peers{bucket}. Run `{self.pipeline.store.run_id}`, method "
                      f"{self.setup.name}.")
        self.pipeline.research()
        return [intro, *self._view(scope_replies(self.pipeline))]

    def _resume_stage(self, message: str, progress: Progress) -> list[Reply]:
        if message.strip().lower() != "resume":
            return [Reply(f"This run stopped while {self.status}. Say **resume** to run that "
                          "stage again.")]
        if self.status in ("created", "researching"):
            self.pipeline.research()
            return self._view(scope_replies(self.pipeline)) or [Reply("Research finished.")]
        self.pipeline.analyse()
        return self._view(findings_replies(self.pipeline)) or [Reply("Analysis finished.")]

    # Checkpoint 1

    def _scope_state(self) -> str:
        p = self.pipeline
        peers = p.peers
        lines = [f"Client: {p.profile.short_name} (id: client)",
                 f"Market-cap filter: {peers.bucket_filter or 'none'}; buckets: " + "; ".join(
                     b.describe(p.setup.bucket_currency) for b in p.setup.buckets),
                 f"Peers wanted: {peers.peer_count}", "Peer candidates:"]
        for c in peers.candidates:
            lines.append(f"- {c.id} [{'selected' if c.selected else 'not selected'}] {c.name} "
                         f"({c.listing_label()}, bucket {c.bucket or '?'}, disclosure "
                         f"{p.availability(c.id)}, {'AI recommended' if c.recommended else c.added_by})")
        lines.append("Documents:")
        lines += [f"- {d.id} ({d.company_id}, {d.doc_type}, FY{d.fiscal_year}, {d.status})"
                  for d in p.documents]
        return "\n".join(lines)

    def _scope(self, message: str, progress: Progress) -> list[Reply]:
        p = self.pipeline
        if GO.match(message):
            return self._lock_and_analyse(progress)
        cmds = p.llm.structured(
            stage="chat_scope", prompt_name=p.setup.prompt_version("scope_command"),
            prompt=p.setup.prompt("scope_command", state=self._scope_state(), message=message),
            schema=ScopeCommands, effort=p.setup.effort("command"))
        replies = [Reply(cmds.reply)] if cmds.reply else []
        changed = False
        for cmd in cmds.commands:
            if cmd.action == "lock":
                return replies + self._lock_and_analyse(progress)
            if cmd.action in ("select", "deselect"):
                if not p.resolve_peers(cmd.targets):
                    replies.append(Reply(f"No candidate matched {', '.join(cmd.targets)}."))
                changed |= bool(p.select(cmd.targets, selected=cmd.action == "select"))
            elif cmd.action == "add_peer" and cmd.value:
                cand = p.add_peer(cmd.value)
                replies.append(Reply(f"Added {cand.name} as {cand.id}."))
                changed = True
            elif cmd.action == "set_bucket":
                value = (cmd.value or "").lower()
                p.set_bucket(value if value in ("large", "mid", "small") else None)
                changed = True
            elif cmd.action == "set_peer_count" and cmd.value and cmd.value.strip().isdigit():
                p.set_peer_count(int(cmd.value))
                changed = True
            elif cmd.action == "add_document" and cmd.value:
                company = (cmd.targets or [CLIENT])[0]
                company = CLIENT if company.lower() == CLIENT else company.upper()
                doc = p.add_document(company, url=cmd.value.strip())
                replies.append(Reply(f"Added {doc.id}: {doc.status}"
                                     + (f" ({doc.note})" if doc.note else "")))
                changed = True
            elif cmd.action == "mark_na":
                for doc_id in cmd.targets:
                    p.mark_na(doc_id.upper(), cmd.value or "marked n/a by consultant")
                changed = True
            elif cmd.action == "show":
                return replies + self._view(scope_replies(p))
        if changed:
            replies += self._view([peers_reply(p), sources_reply(p),
                                   Reply("Anything else? Say **go** when ready.")])
        return replies or [Reply("I didn't catch a change. Say **go** to lock the scope.")]

    def _lock_and_analyse(self, progress: Progress) -> list[Reply]:
        p = self.pipeline
        scope = p.lock_scope()
        missing = [p.company_name(cid) for cid, doc in scope["documents"].items() if doc is None]
        notes = [Reply("Scope locked. Reading the risk sections of the client and "
                       f"{len(scope['peer_ids'])} peers." + (
                           f" No usable document for {', '.join(missing)}: marked insufficient "
                           "evidence." if missing else ""))]
        p.analyse()
        return notes + self._view(findings_replies(p))

    # Checkpoint 2

    def _findings_state(self) -> str:
        p = self.pipeline
        f = p.findings()
        lines = ["Companies: " + ", ".join(f"{c.id}={c.name}" for c in f.companies),
                 "Taxonomy category keys: " + ", ".join(p.setup.taxonomy_keys), "Themes:"]
        for t in f.themes:
            ids = [r.id for r in f.risks if r.theme_key == t.key]
            lines.append(f"- {t.key}: {t.name} [{t.category}]{' (removed)' if t.removed else ''}"
                         f" risks: {', '.join(ids) or 'none'}")
        if f.remarks:
            lines.append("Remarks: " + " | ".join(f.remarks))
        return "\n".join(lines)

    def _findings(self, message: str) -> list[Reply]:
        p = self.pipeline
        if EXPORT.match(message):
            return [self._export()]
        cmds = p.llm.structured(
            stage="chat_findings", prompt_name=p.setup.prompt_version("findings_command"),
            prompt=p.setup.prompt("findings_command", state=self._findings_state(),
                                  message=message),
            schema=FindingsCommands, effort=p.setup.effort("command"))
        replies = [Reply(cmds.reply)] if cmds.reply else []
        changed = False

        def apply(done: list, what: str) -> None:
            nonlocal changed
            if done:
                changed = True
            else:
                replies.append(Reply(f"No {what} matched {', '.join(cmd.targets) or 'that'}."))

        for cmd in cmds.commands:
            if cmd.action == "remove_theme":
                apply(p.remove_themes(cmd.targets), "theme")
            elif cmd.action == "restore_theme":
                apply(p.restore_themes(cmd.targets), "removed theme")
            elif cmd.action == "rename_theme" and cmd.targets and cmd.value:
                apply(p.rename_theme(cmd.targets[0], cmd.value), "theme")
            elif cmd.action == "recategorise_theme" and cmd.targets and cmd.value:
                apply(p.recategorise_theme(cmd.targets[0], cmd.value), "theme")
            elif cmd.action == "reject_evidence":
                apply(p.reject_evidence(cmd.targets), "risk id")
            elif cmd.action == "add_remark" and cmd.value:
                p.add_remark(cmd.value)
                changed = True
            elif cmd.action == "export":
                replies.append(self._export())
            elif cmd.action == "show":
                if cmd.targets:
                    replies += self._view([evidence_reply(p, cmd.targets)])
                else:
                    replies += self._view(findings_replies(p))
        if changed:
            replies += self._view(findings_replies(p))
        return replies or [Reply("I didn't catch a change. Say **export** for the workbook.")]

    def _export(self) -> Reply:
        path = self.pipeline.export()
        return Reply(f"Exported the Product A workbook: `{path.name}`.", file=str(path))
