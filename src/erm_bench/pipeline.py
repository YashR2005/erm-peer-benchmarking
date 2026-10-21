"""Product A pipeline: the fixed stages of user flow v2.1, over the run store.

Stages and checkpoints (user-flow-v2.md):
  2 research()       profile, peer scan, light disclosure scan
  3 checkpoint 1     select / add_peer / set_bucket / add_document / mark_na / lock_scope
  4 analyse()        A1 context risks, A2 extraction, A3 verification, A4 mapping
  5 checkpoint 2     remove / rename / recategorise themes, reject evidence, remarks
    export()         Excel workbook

UI-agnostic: the chat app and the CLI both drive this class.
"""

from __future__ import annotations

import functools
import re
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import documents as docs
from .llm import LLM, CallCounter, ClaudeLLM, PromptTooLarge
from .product_a import (
    CLIENT,
    Company,
    Gap,
    MatrixRow,
    StructureComparison,
    build_matrix,
    client_has_disclosure,
    compare_structures,
    find_gaps,
)
from .schemas import (
    Availability,
    CompanyCategory,
    ContextRisk,
    ContextRisksDraft,
    DisclosedRisk,
    DisclosureDraft,
    DocChecks,
    Document,
    Extraction,
    ExtractionDraft,
    Fact,
    MappingDraft,
    PeerCandidate,
    PeerScanDraft,
    PeerSet,
    Profile,
    ProfileDraft,
    Request,
    RequestDraft,
    Theme,
)
from .spend import Budget, BudgetExceeded
from .standard_setup import StandardSetup
from .store import DEFAULT_RUNS_DIR, RunStore, now_iso
from .timing import Clock, StopRequested, TimeLimitExceeded
from .verify import normalize_pages, verify_quote

Progress = Callable[[str], None]
LLMFactory = Callable[[StandardSetup, RunStore | None], LLM] | None

UNMAPPED = Theme(key="unmapped", name="Not mapped (review)", category="other",
                 description="Risks the mapping step did not assign to a theme.")


class ScopeError(ValueError):
    pass


def reports_llm_use(stage: str):
    """Print the run's model calls, tokens, cost and failed steps when the stage ends."""
    def wrap(method):
        @functools.wraps(method)
        def run(self: Pipeline, *args, **kwargs):
            outcome = "finished"
            try:
                return method(self, *args, **kwargs)
            except BaseException as e:
                outcome = f"stopped: {e}"
                raise
            finally:
                lines = getattr(self.llm, "summary_lines", None)
                if lines:
                    print(f"\n=== Run {self.store.run_id} · {stage} {outcome} ===\n"
                          + "\n".join(lines()) + "\n", file=sys.stderr, flush=True)
        return run
    return wrap


def make_llm(setup: StandardSetup, store: RunStore | None = None, client=None) -> LLM:
    """The backend named in the standard setup (or ERM_LLM_PROVIDER). The run's spending and
    call caps apply, counting what the run (with a store) has already used. `client` replaces
    the Claude API client (the dry run passes a fake)."""
    log = store.log_llm_call if store else None
    records = store.read_log("llm_calls.jsonl") if store else []
    budget = Budget(setup, records)
    system = setup.prompt("system")
    if setup.provider == "anthropic":
        cfg = setup.provider_settings()
        return ClaudeLLM(model=setup.model, system=system, log=log, client=client,
                         web_search_tool=cfg.get("web_search", "web_search_20250305"),
                         web_fetch_tool=cfg.get("web_fetch", "web_fetch_20250910"),
                         max_fetch_tokens=setup.max_fetch_tokens, budget=budget,
                         server_fallback=setup.model in cfg.get("server_fallback_models", []),
                         calls=CallCounter(already=sum(len(r.get("usage") or []) for r in records)))
    if setup.provider == "openrouter":
        from .llm_openrouter import OpenRouterLLM

        cfg = setup.provider_settings()
        return OpenRouterLLM(model=setup.model, system=system, log=log,
                             base_url=cfg.get("base_url", "https://openrouter.ai/api/v1"),
                             web_search=cfg.get("web_search"), budget=budget)
    raise ValueError(f"Unknown LLM provider '{setup.provider}'")


def parse_request(message: str, llm: LLM, setup: StandardSetup) -> RequestDraft:
    return llm.structured(
        stage="request", prompt_name=setup.prompt_version("parse_request"),
        prompt=setup.prompt("parse_request", message=message, today=date.today().isoformat()),
        schema=RequestDraft, effort=setup.effort("command"))


def request_from_draft(draft: RequestDraft, setup: StandardSetup) -> Request:
    return Request(
        client_name=draft.client_name or "",
        reporting_year=draft.reporting_year,
        sector=draft.sector,
        focus_regions=draft.focus_regions,
        notes=draft.notes or "",
        peer_count=draft.peer_count or setup.method["peers"]["default_count"],
        market_cap_bucket=draft.market_cap_bucket,
        include=draft.include,
        exclude=draft.exclude,
    )


@dataclass
class Findings:
    companies: list[Company]
    rows: list[MatrixRow]
    gaps: list[Gap]
    structure: StructureComparison
    client_disclosed: bool
    context_risks: list[ContextRisk]
    extractions: list[Extraction]
    themes: list[Theme]
    remarks: list[str]

    @property
    def risks(self) -> list[DisclosedRisk]:
        return [r for e in self.extractions for r in e.risks]


class Pipeline:
    def __init__(self, store: RunStore, setup: StandardSetup, llm: LLM,
                 fetch: docs.Fetcher | None = None, progress: Progress | None = None,
                 max_workers: int = 3, stop: threading.Event | None = None):
        self.store = store
        self.setup = setup
        self.llm = llm
        max_mb = setup.method["documents"]["max_download_mb"]
        self.fetch = fetch or (lambda url: docs.http_fetch(url, max_mb=max_mb))
        self.progress = progress or (lambda message: None)
        self.max_workers = max_workers
        # One clock for this pipeline's stages, shared with the model backend's calls.
        # `stop` is the Stop button: set it and the run stops at the next check.
        self.clock = Clock(per_call=setup.time_limit("per_call"), stop=stop)
        if hasattr(llm, "clock"):
            llm.clock = self.clock

    # ------------------------------------------------------------------
    # Creating and opening runs

    @classmethod
    def start(cls, request: Request, setup: StandardSetup, llm_factory: LLMFactory = None,
              runs_dir: Path = DEFAULT_RUNS_DIR, **kwargs) -> Pipeline:
        if (cap := setup.method["peers"].get("max_count")) and request.peer_count > cap:
            request = request.model_copy(update={"peer_count": cap})
        store = RunStore.create(request.client_name, {
            "setup_version": setup.version, "method": setup.name, "provider": setup.provider,
            "model": setup.model, "request": request,
        }, runs_dir=runs_dir)
        store.save("request", request)
        return cls(store, setup, (llm_factory or make_llm)(setup, store), **kwargs)

    @classmethod
    def resume(cls, run_id: str, llm_factory: LLMFactory = None,
               runs_dir: Path = DEFAULT_RUNS_DIR, **kwargs) -> Pipeline:
        store = RunStore.open(run_id, runs_dir=runs_dir)
        setup = StandardSetup.load(store.meta["setup_version"])
        return cls(store, setup, (llm_factory or make_llm)(setup, store), **kwargs)

    # ------------------------------------------------------------------
    # Stage artifacts

    @property
    def request(self) -> Request:
        return self.store.load("request", Request)

    @property
    def profile(self) -> Profile:
        return self.store.load("profile", Profile)

    @property
    def peers(self) -> PeerSet:
        return self.store.load("peers", PeerSet)

    @property
    def documents(self) -> list[Document]:
        return self.store.load("documents", list[Document]) if self.store.has("documents") else []

    @property
    def scope(self) -> dict:
        return self.store.load("scope", dict)

    @property
    def wanted_year(self) -> int | None:
        if self.request.reporting_year:
            return self.request.reporting_year
        return self.profile.latest_annual_report_year if self.store.has("profile") else None

    def companies(self) -> list[Company]:
        """Client plus selected peers (the locked set once scope is locked)."""
        profile, peers = self.profile, self.peers
        ids = self.scope["peer_ids"] if self.store.has("scope") else [p.id for p in peers.selected]
        return [Company(CLIENT, profile.short_name)] + [
            Company(p.id, p.name) for p in peers.candidates if p.id in ids]

    def company_name(self, company_id: str) -> str:
        if company_id == CLIENT:
            return self.profile.short_name
        peer = self.peers.get(company_id)
        return peer.name if peer else company_id

    def _set(self, status: str) -> None:
        self.store.set_status(status)

    # ------------------------------------------------------------------
    # Stage 2 · Research

    @reports_llm_use("research")
    def research(self) -> None:
        """Profile, peer scan and light disclosure scan. Each piece is saved when it finishes,
        so running this again after a failure or time limit continues where it stopped."""
        self._set("researching")
        req = self.request
        with self.clock.stage("research", self.setup.time_limit("research")):
            if self.store.has("profile"):
                profile = self.profile
                self.progress("Using the client profile from the earlier attempt")
            else:
                self.clock.check()
                self.progress(f"Profiling {req.client_name} from live sources")
                profile = self._profile(req)
                self.store.save("profile", profile)

            if self.store.has("peers"):
                peers = self.peers
                self.progress("Using the peer candidates from the earlier attempt")
            else:
                self.clock.check()
                self.progress("Scanning for listed peers")
                peers = self._scan_peers(profile, req.market_cap_bucket, req.peer_count)
                self.store.save("peers", peers)

            scanned = {d.company_id for d in self.documents}
            targets = [(CLIENT, profile.short_name, [profile.legal_name, profile.short_name],
                        profile.listing_label())]
            targets += [(p.id, p.name, [p.legal_name, p.name], p.listing_label())
                        for p in peers.candidates]
            targets = [t for t in targets if t[0] not in scanned]
            if targets:
                self.progress(f"Light disclosure scan: {len(targets)} "
                              f"{'company' if len(targets) == 1 else 'companies'}")
                self._light_scan(targets)
        self._set("awaiting_scope")

    def _profile(self, req: Request) -> Profile:
        s = self.setup
        notes = self.llm.research(
            stage="profile", prompt_name=s.prompt_version("profile_research"),
            prompt=s.prompt("profile_research", client_name=req.client_name,
                            context=req.context_line(),
                            reporting_year=req.reporting_year or "latest available"),
            effort=s.effort("research"), **s.research_budget("profile"))
        draft = self.llm.structured(
            stage="profile_structure", prompt_name=s.prompt_version("profile_structure"),
            prompt=s.prompt("profile_structure", notes=notes.text,
                            citations=notes.citations_block()),
            schema=ProfileDraft, effort=s.effort("structure"))

        facts = [Fact(id=f"F{i}", field=f.field, text=f.text, sources=notes.resolve(f.citation_ids))
                 for i, f in enumerate(draft.facts, start=1)]
        guide = s.sector_guide_for(req.sector, draft.sector, draft.business_description,
                                   " ".join(draft.segments))
        warnings = []
        unsourced = [f.id for f in facts if not f.sourced]
        if unsourced:
            warnings.append(f"{len(unsourced)} fact(s) have no source and are marked unsourced: "
                            f"{', '.join(unsourced)}")
        if not draft.listed:
            warnings.append("The client appears to be unlisted. Unlisted companies rarely publish "
                            "risk management information; consider 'peer reference only'.")
        if draft.related_listed_entities:
            warnings.append("Related listed entities (their disclosures are not credited to the "
                            f"client): {', '.join(draft.related_listed_entities)}")
        if draft.market_cap_sgd_millions is None and draft.listed:
            warnings.append("Market capitalisation not found; the client's bucket is unknown.")
        fields = tidy_figures(draft.model_dump(exclude={"facts"}))
        return Profile(**fields, bucket=s.bucket_for(draft.market_cap_sgd_millions),
                       sector_guide=guide.key, facts=facts, warnings=warnings)

    def _profile_summary(self, profile: Profile) -> str:
        lines = [
            f"Name: {profile.legal_name} ({profile.listing_label()})",
            f"Sector: {profile.sector}",
            f"Business: {profile.business_description}",
            f"Segments: {', '.join(profile.segments) or 'not found'}",
            f"Countries: {', '.join(profile.countries) or 'not found'}",
            f"Main markets: {', '.join(profile.main_markets) or 'not found'}",
            f"Market cap: {format_money(profile.market_cap_sgd_millions)} (as of {profile.market_cap_as_of})",
            f"Total assets: {profile.total_assets}; revenue: {profile.revenue}; "
            f"EBITDA: {profile.ebitda}; employees: {profile.employees}",
            f"Parent: {profile.parent or 'none found'}",
            f"Related listed entities (not peers): {', '.join(profile.related_listed_entities) or 'none'}",
        ]
        return "\n".join(lines)

    def _bucket_rule(self, bucket: str | None) -> str:
        if not bucket:
            return "none; consider all market capitalisations."
        b = self.setup.bucket(bucket)
        return (f"only propose companies in this bucket: {b.describe(self.setup.bucket_currency)}. "
                "If fewer suitable companies exist in it, say so rather than going outside it.")

    def _scan_peers(self, profile: Profile, bucket: str | None, peer_count: int,
                    keep: list[PeerCandidate] | None = None, next_id: int = 1) -> PeerSet:
        s, req = self.setup, self.request
        candidate_count = peer_count * s.method["peers"]["candidate_multiplier"]
        guide = next(g for g in s.sector_guides if g.key == profile.sector_guide)
        notes = self.llm.research(
            stage="peers", prompt_name=s.prompt_version("peers_research"),
            prompt=s.prompt(
                "peers_research", profile=self._profile_summary(profile), peer_count=peer_count,
                candidate_count=candidate_count, listed_only=s.method["peers"]["listed_only"],
                bucket_rule=self._bucket_rule(bucket),
                regions=", ".join(req.focus_regions) or "none given",
                include=", ".join(req.include) or "none",
                exclude=", ".join(req.exclude) or "none",
                peer_hints="\n".join(f"- {h}" for h in guide.peer_hints)),
            effort=s.effort("research"), **s.research_budget("peers"))
        draft = self.llm.structured(
            stage="peers_structure", prompt_name=s.prompt_version("peers_structure"),
            prompt=s.prompt("peers_structure", notes=notes.text, citations=notes.citations_block(),
                            candidate_count=candidate_count, peer_count=peer_count),
            schema=PeerScanDraft, effort=s.effort("structure"))

        excluded = {_name_key(n) for n in [profile.legal_name, profile.short_name, profile.parent or "",
                                           *profile.related_listed_entities, *req.exclude] if n}
        kept = keep or []
        candidates = list(kept)
        n = next_id
        for c in draft.candidates:
            if _name_key(c.name) in excluded or _name_key(c.legal_name) in excluded:
                continue
            if any(_name_key(c.name) == _name_key(k.name) for k in candidates):
                continue
            candidates.append(self._candidate(f"P{n}", c, notes.resolve(c.citation_ids), bucket))
            n += 1

        # Recommended first; select the requested number among AI candidates.
        ai = sorted([c for c in candidates if c.added_by == "ai"], key=lambda c: not c.recommended)
        if cap := s.method["peers"].get("max_count"):  # trial cap: scan no more peers than this
            ai = ai[:max(cap - len(kept), 0)]
        room = max(peer_count - sum(1 for k in kept if k.selected), 0)
        for i, c in enumerate(ai):
            c.selected = i < room
        scan_notes = draft.scan_notes
        if bucket:
            in_bucket = sum(1 for c in candidates if c.bucket == bucket)
            if in_bucket < peer_count:
                scan_notes += (f" Only {in_bucket} candidate(s) found in the {bucket}-cap bucket; "
                               "consider widening to the neighbouring bucket.")
        return PeerSet(bucket_filter=bucket, peer_count=peer_count, scan_notes=scan_notes.strip(),
                       candidates=kept + [c for c in ai])

    def _candidate(self, peer_id: str, draft, sources, bucket: str | None) -> PeerCandidate:
        cand = PeerCandidate(id=peer_id, **tidy_figures(draft.model_dump(exclude={"citation_ids"})),
                             bucket=self.setup.bucket_for(draft.market_cap_sgd_millions),
                             sources=sources)
        if not cand.listed:
            cand.warnings.append("Unlisted: disclosure is likely to be thin.")
        if bucket and cand.bucket and cand.bucket != bucket:
            cand.warnings.append(f"Outside the requested {bucket}-cap bucket ({cand.bucket}).")
        if cand.market_cap_sgd_millions is None:
            cand.warnings.append("Market cap not found.")
        if not sources:
            cand.warnings.append("No cited source for this candidate's figures.")
        if cand.related_entity_warning:
            cand.warnings.append(cand.related_entity_warning)
        return cand

    # Light disclosure scan

    def _light_scan(self, targets: list[tuple[str, str, list[str], str]]) -> list[Document]:
        """Scan companies in parallel, saving each company's documents as soon as it's done."""
        found: list[Document] = []
        error: Exception | None = None
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = [pool.submit(self._scan_company, *t) for t in targets]
            for future in as_completed(futures):
                try:
                    company_docs = future.result()
                except Exception as e:  # a spending or time limit: keep what others finished
                    error = error or e
                    continue
                self.store.save("documents", self.documents + company_docs)
                found += company_docs
        if error:
            raise error
        return found

    def _scan_company(self, company_id: str, name: str, names: list[str],
                      listing: str) -> list[Document]:
        s = self.setup
        self.clock.check()
        try:
            notes = self.llm.research(
                stage=f"disclosure:{company_id}", prompt_name=s.prompt_version("disclosure_research"),
                prompt=s.prompt("disclosure_research", company_name=name, listing=listing,
                                reporting_year=self.wanted_year or "latest available"),
                effort=s.effort("research"), **s.research_budget("disclosure"))
            draft = self.llm.structured(
                stage=f"disclosure_structure:{company_id}",
                prompt_name=s.prompt_version("disclosure_structure"),
                prompt=s.prompt("disclosure_structure", company_name=name, notes=notes.text,
                                citations=notes.citations_block()),
                schema=DisclosureDraft, effort=s.effort("structure"))
        except (BudgetExceeded, TimeLimitExceeded, StopRequested):
            raise  # spending and time limits, and Stop, end the whole stage
        except Exception as e:  # one company's failure must not stop the scan
            return [Document(id=f"D-{company_id}-1", company_id=company_id,
                             doc_type="annual_report", title="(disclosure scan failed)",
                             status="download_failed", note=f"{type(e).__name__}: {e}")]

        self.clock.check()  # don't start downloads after the stage's time limit
        found = [Document(id=f"D-{company_id}-{i}", company_id=company_id, doc_type=d.doc_type,
                          title=d.title, fiscal_year=d.fiscal_year, url=d.url)
                 for i, d in enumerate(draft.documents, start=1)]
        if not found:
            return [Document(id=f"D-{company_id}-1", company_id=company_id,
                             doc_type="annual_report", title="(no annual report found)",
                             status="download_failed", note=draft.notes)]

        wanted = self.wanted_year
        reports = sorted([d for d in found if d.doc_type == "annual_report"],
                         key=lambda d: (d.fiscal_year != wanted, -(d.fiscal_year or 0)))
        # Download the best annual report (try a second if the first fails) and any
        # separate corporate governance report. Sustainability reports are listed only.
        for doc in reports[:2]:
            self._fetch_and_check(doc, names)
            if doc.status == "ok":
                break
        for doc in found:
            if doc.doc_type == "corporate_governance_report":
                self._fetch_and_check(doc, names)
        return found

    def _fetch_and_check(self, doc: Document, names: list[str], data: bytes | None = None) -> None:
        if data is None:
            try:
                data = self.fetch(doc.url)
            except docs.DownloadError as e:
                doc.status, doc.note = "download_failed", str(e)
                return
        try:
            stored = docs.store_pdf(data, self.store.sources_dir)
        except docs.NotPdfError as e:
            stored = self._follow_attachment(doc, data) if doc.url else None
            if stored is None:
                doc.status, doc.note = "not_pdf", str(e)
                return
        method = self.setup.method["documents"]
        pages = docs.cached_pages(stored.path, self.store.pages_dir)
        sections = docs.locate_risk_sections(
            pages, docs.read_toc(stored.path), self.setup.filing_sections["annual_report"],
            max_pages=method["max_risk_pages"])
        year = docs.find_report_year(pages, method["first_pages"])
        doc.sha256, doc.page_count, doc.risk_pages = stored.sha256, len(pages), sections.pages
        doc.checks = DocChecks(
            company_match=docs.company_matches(pages, names, method["first_pages"]),
            year_found=year, year_match=docs.year_matches(year, self.wanted_year),
            has_risk_section=sections.has_section, low_text_pages=docs.low_text_pages(pages))
        notes = []
        if not doc.checks.company_match:
            notes.append("company name not found on the first pages")
        if doc.checks.year_match is False:
            notes.append(f"looks like {year}, wanted {self.wanted_year}")
        if not sections.has_section:
            notes.append("no risk management section heading found")
        if doc.checks.low_text_pages:
            notes.append(f"{doc.checks.low_text_pages} page(s) without a text layer (OCR not set up)")
        doc.note = "; ".join(notes)
        doc.status = "ok" if doc.checks.company_match else "check_failed"

    def _follow_attachment(self, doc: Document, html_bytes: bytes) -> docs.StoredPdf | None:
        """The link was a web page (e.g. an SGX announcement): try its PDF attachments, best
        match for the document type first."""
        html = html_bytes.decode("utf-8", errors="ignore")
        for link in docs.rank_attachments(docs.pdf_links(html, doc.url), doc.doc_type)[:2]:
            try:
                stored = docs.store_pdf(self.fetch(link), self.store.sources_dir)
            except docs.DownloadError:
                continue
            doc.url = link
            return stored
        return None

    def availability(self, company_id: str) -> Availability:
        usable = [d for d in self.documents
                  if d.company_id == company_id and d.status in ("ok", "uploaded")]
        if any(d.checks.has_risk_section for d in usable):
            return "good"
        return "partial" if usable else "none"

    def primary_document(self, company_id: str) -> Document | None:
        usable = [d for d in self.documents
                  if d.company_id == company_id and d.status in ("ok", "uploaded") and d.sha256]
        rank = {"annual_report": 0, "corporate_governance_report": 1}
        usable.sort(key=lambda d: (d.status != "uploaded", not d.checks.has_risk_section,
                                   rank.get(d.doc_type, 2), d.checks.year_match is False))
        return usable[0] if usable else None

    # ------------------------------------------------------------------
    # Checkpoint 1 · Confirm scope

    def _require_status(self, *allowed: str) -> None:
        if self.store.status not in allowed:
            raise ScopeError(f"Not possible while the run is '{self.store.status}'.")

    def resolve_peers(self, targets: list[str]) -> list[PeerCandidate]:
        peers, found = self.peers, []
        for t in targets:
            key = t.strip().lower()
            match = peers.get(t.strip().upper()) or next(
                (c for c in peers.candidates
                 if key and (key in c.name.lower() or key in c.legal_name.lower())), None)
            if match and match not in found:
                found.append(match)
        return found

    def select(self, targets: list[str], selected: bool = True) -> list[PeerCandidate]:
        self._require_status("awaiting_scope")
        peers = self.peers
        changed = []
        for cand in self.resolve_peers(targets):
            c = peers.get(cand.id)
            if c.selected != selected:
                self.store.log_review("select" if selected else "deselect", c.id,
                                      before=c.selected, after=selected)
                c.selected = selected
                changed.append(c)
        self.store.save("peers", peers)
        return changed

    def set_peer_count(self, count: int) -> None:
        self._require_status("awaiting_scope")
        if (cap := self.setup.method["peers"].get("max_count")) and count > cap:
            raise ScopeError(f"At most {cap} peers (peers.max_count in "
                             f"setup/{self.setup.version}/method.yaml).")
        peers = self.peers
        before = peers.peer_count
        peers.peer_count = count
        ordered = sorted(peers.candidates, key=lambda c: (c.added_by != "consultant",
                                                          not c.recommended))
        for i, c in enumerate(ordered):
            c.selected = i < count
        self.store.save("peers", peers)
        self.store.log_review("set_peer_count", "peers", before=before, after=count)

    def add_peer(self, name: str) -> PeerCandidate:
        self._require_status("awaiting_scope")
        with self.clock.stage("research", self.setup.time_limit("research")):
            return self._add_peer(name)

    def _add_peer(self, name: str) -> PeerCandidate:
        s, profile, peers = self.setup, self.profile, self.peers
        self.progress(f"Researching {name}")
        notes = self.llm.research(
            stage="peer_lookup", prompt_name=s.prompt_version("peer_lookup"),
            prompt=s.prompt("peer_lookup", company_name=name,
                            profile=self._profile_summary(profile)),
            effort=s.effort("research"), **s.research_budget("peer_lookup"))
        draft = self.llm.structured(
            stage="peer_lookup_structure", prompt_name=s.prompt_version("peers_structure"),
            prompt=s.prompt("peers_structure", notes=notes.text, citations=notes.citations_block(),
                            candidate_count=1, peer_count=1),
            schema=PeerScanDraft, effort=s.effort("structure"))
        if not draft.candidates:
            raise ScopeError(f"Couldn't find enough about {name} to add it.")
        cand = self._candidate(f"P{self._next_peer_number(peers)}", draft.candidates[0],
                               notes.resolve(draft.candidates[0].citation_ids), peers.bucket_filter)
        cand.added_by, cand.selected, cand.recommended = "consultant", True, False
        peers.candidates.append(cand)
        self.store.save("peers", peers)
        self.store.log_review("add_peer", cand.id, after=cand.name)
        self.progress(f"Light disclosure scan: {cand.name}")
        self._light_scan([(cand.id, cand.name, [cand.legal_name, cand.name], cand.listing_label())])
        return cand

    def set_bucket(self, bucket: str | None) -> PeerSet:
        """Change the market-cap filter and re-run the peer scan. Consultant-added peers stay."""
        self._require_status("awaiting_scope")
        old = self.peers
        kept = [c for c in old.candidates if c.added_by == "consultant"]
        with self.clock.stage("research", self.setup.time_limit("research")):
            self.progress("Re-running the peer scan" + (f" for {bucket} caps" if bucket else ""))
            peers = self._scan_peers(self.profile, bucket, old.peer_count, keep=kept,
                                     next_id=self._next_peer_number(old))
            kept_ids = {c.id for c in kept} | {CLIENT}
            self.store.save("peers", peers)
            self.store.save("documents", [d for d in self.documents if d.company_id in kept_ids])
            self.store.log_review("set_bucket", "peers", before=old.bucket_filter, after=bucket)
            new = [c for c in peers.candidates if c.id not in kept_ids]
            self.progress(f"Light disclosure scan: {len(new)} new candidates")
            self._light_scan([(c.id, c.name, [c.legal_name, c.name], c.listing_label())
                              for c in new])
        return peers

    @staticmethod
    def _next_peer_number(peers: PeerSet) -> int:
        numbers = [int(c.id[1:]) for c in peers.candidates if c.id[1:].isdigit()]
        return max(numbers, default=0) + 1

    def _company_names(self, company_id: str) -> list[str]:
        if company_id == CLIENT:
            p = self.profile
            return [p.legal_name, p.short_name]
        c = self.peers.get(company_id)
        if c is None:
            raise ScopeError(f"Unknown company {company_id}")
        return [c.legal_name, c.name]

    def add_document(self, company_id: str, url: str | None = None, data: bytes | None = None,
                     title: str = "Supplied by consultant") -> Document:
        """A document the consultant supplies, by URL or upload. Accepted even if a check fails;
        the check results stay visible."""
        self._require_status("awaiting_scope")
        documents = self.documents
        n = sum(1 for d in documents if d.company_id == company_id) + 1
        doc = Document(id=f"D-{company_id}-{n}", company_id=company_id, doc_type="annual_report",
                       title=title, url=url, fiscal_year=self.wanted_year)
        self._fetch_and_check(doc, self._company_names(company_id), data=data)
        if doc.sha256:
            doc.status = "uploaded"
        documents.append(doc)
        self.store.save("documents", documents)
        self.store.log_review("add_document", doc.id, after=url or title)
        return doc

    def mark_na(self, doc_id: str, reason: str) -> None:
        self._require_status("awaiting_scope")
        documents = self.documents
        doc = next((d for d in documents if d.id == doc_id), None)
        if doc is None:
            raise ScopeError(f"Unknown document {doc_id}")
        if doc.status == "na":
            return
        self.store.log_review("mark_na", doc_id, before=doc.status, after="na", reason=reason)
        doc.previous_status, doc.previous_note = doc.status, doc.note
        doc.status, doc.note = "na", reason
        self.store.save("documents", documents)

    def restore_document(self, doc_id: str) -> None:
        """Undo "Mark n/a"."""
        self._require_status("awaiting_scope")
        documents = self.documents
        doc = next((d for d in documents if d.id == doc_id), None)
        if doc is None:
            raise ScopeError(f"Unknown document {doc_id}")
        if doc.status != "na":
            raise ScopeError(f"{doc_id} isn't marked not available.")
        if doc.previous_status:
            status = doc.previous_status
        elif doc.sha256:  # marked before statuses were remembered: work it out from the checks
            status = "ok" if doc.checks.company_match else "check_failed"
        else:
            status = "found"
        self.store.log_review("restore_document", doc_id, before="na", after=status)
        doc.status, doc.note = status, doc.previous_note
        doc.previous_status, doc.previous_note = None, ""
        self.store.save("documents", documents)

    def lock_scope(self) -> dict:
        self._require_status("awaiting_scope")
        selected = self.peers.selected
        if not selected:
            raise ScopeError("Select at least one peer before locking the scope.")
        if (cap := self.setup.method["peers"].get("max_count")) and len(selected) > cap:
            raise ScopeError(f"Select at most {cap} peers (peers.max_count in "
                             f"setup/{self.setup.version}/method.yaml).")
        ids = [CLIENT] + [p.id for p in selected]
        chosen = {cid: (d.id if (d := self.primary_document(cid)) else None) for cid in ids}
        scope = {"locked_at": now_iso(), "peer_ids": [p.id for p in selected],
                 "bucket_filter": self.peers.bucket_filter, "documents": chosen}
        self.store.save("scope", scope)
        self.store.log_review("lock_scope", "scope", after=scope)
        self._set("analysing")
        return scope

    # ------------------------------------------------------------------
    # Stage 4 · Analysis

    @reports_llm_use("analysis")
    def analyse(self) -> None:
        """Read each company's risk section and map the results. Each company's extraction is
        saved when it finishes, so running this again continues where it stopped."""
        self._require_status("analysing")
        companies = self.companies()
        by_id = {d.id: d for d in self.documents}
        scope_docs = self.scope["documents"]
        with self.clock.stage("analysis", self.setup.time_limit("analysis")):
            done = {c.id: self.store.load(f"extract-{c.id}", Extraction) for c in companies
                    if self.store.has(f"extract-{c.id}")}
            todo = [c for c in companies if c.id not in done]
            context_risks = (self.store.load("context_risks", list[ContextRisk])
                             if self.store.has("context_risks") else None)
            if todo:
                self.progress("Reading risk sections of " + ", ".join(c.name for c in todo))
            error: Exception | None = None
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                ctx_future = (pool.submit(self._context_risks, self.profile)
                              if context_risks is None else None)
                futures = {pool.submit(self._extract, c, by_id.get(scope_docs.get(c.id) or "")): c
                           for c in todo}
                for future in as_completed(futures):
                    company = futures[future]
                    try:
                        done[company.id] = future.result()
                    except Exception as e:  # keep what the other companies finished
                        error = error or e
                        continue
                    self.store.save(f"extract-{company.id}", done[company.id])
                if ctx_future:
                    try:
                        context_risks = ctx_future.result()
                        self.store.save("context_risks", context_risks)
                    except Exception as e:
                        error = error or e
            if error:
                raise error
            extractions = [done[c.id] for c in companies]
            self.clock.check()
            self.progress("Mapping risks to a common set of themes")
            themes = self._map(context_risks, extractions, companies)
        self.store.save("context_risks", context_risks)
        self.store.save("extractions", extractions)
        self.store.save("themes", themes)
        if not self.store.has("remarks"):
            self.store.save("remarks", [])
        self._set("awaiting_findings")

    def _context_risks(self, profile: Profile) -> list[ContextRisk]:
        self.clock.check()
        s = self.setup
        guide = next(g for g in s.sector_guides if g.key == profile.sector_guide)
        facts = "\n".join(f"{f.id}: {f.text}" + ("" if f.sourced else " (unsourced)")
                          for f in profile.facts)
        draft = self.llm.structured(
            stage="context_risks", prompt_name=s.prompt_version("context_risks"),
            prompt=s.prompt("context_risks", facts=facts,
                            typical_risks="\n".join(f"- {r}" for r in guide.typical_risks),
                            taxonomy=self._taxonomy_text()),
            schema=ContextRisksDraft, effort=s.effort("context_risks"))
        known = {f.id for f in profile.facts}
        risks = []
        for i, r in enumerate(draft.risks, start=1):
            risk = ContextRisk(id=f"C{i}", **r.model_dump())
            unknown = [f for f in risk.driver_fact_ids if f not in known]
            risk.driver_fact_ids = [f for f in risk.driver_fact_ids if f in known]
            if unknown:
                risk.warnings.append(f"Dropped unknown fact ids: {', '.join(unknown)}")
            if not risk.driver_fact_ids:
                risk.warnings.append("No profile fact supports this risk.")
            if risk.category not in s.taxonomy_keys:
                risk.warnings.append(f"Category '{risk.category}' is not in the taxonomy.")
            risks.append(risk)
        return risks

    def _extract(self, company: Company, doc: Document | None) -> Extraction:
        self.clock.check()
        if doc is None or not doc.sha256:
            return Extraction(company_id=company.id, doc_id=None, has_risk_disclosure=False,
                              note="No usable document: insufficient evidence.")
        if not doc.risk_pages:
            return Extraction(company_id=company.id, doc_id=doc.id, has_risk_disclosure=False,
                              note="No risk section located in the document.")
        s = self.setup
        pages = docs.cached_pages(self.store.sources_dir / f"{doc.sha256}.pdf",
                                  self.store.pages_dir)
        try:
            draft = self.llm.structured(
                stage=f"extract:{company.id}", prompt_name=s.prompt_version("extract_risks"),
                prompt=s.prompt("extract_risks", company_name=company.name, doc_title=doc.title,
                                pages=docs.page_tagged_text(pages, doc.risk_pages)),
                schema=ExtractionDraft, effort=s.effort("extract"))
        except PromptTooLarge as e:  # not sent: skip this company's extraction and say so
            self.progress(str(e))
            return Extraction(company_id=company.id, doc_id=doc.id, has_risk_disclosure=False,
                              note=f"Skipped: the risk section is over 100k tokens. {e}")
        norm = normalize_pages(pages)
        v = s.method["verify"]

        def check(quote: str, page: int):
            return verify_quote(quote, norm, page, v["min_score"], v["min_quote_chars"])

        risks = []
        for i, r in enumerate(draft.risks, start=1):
            result = check(r.quote, r.page)
            risks.append(DisclosedRisk(
                id=f"R-{company.id}-{i}", company_id=company.id, doc_id=doc.id, title=r.title,
                summary=r.summary, company_category=r.company_category, quote=r.quote,
                page=result.found_page or r.page, verification=result))
        categories = []
        for i, k in enumerate(draft.categories, start=1):
            result = check(k.quote, k.page)
            categories.append(CompanyCategory(
                id=f"K-{company.id}-{i}", company_id=company.id, doc_id=doc.id, name=k.name,
                sub_categories=k.sub_categories, quote=k.quote, page=result.found_page or k.page,
                verification=result))
        verified = sum(1 for r in risks if r.verification.verified)
        return Extraction(
            company_id=company.id, doc_id=doc.id, has_risk_disclosure=draft.has_risk_disclosure,
            category_structure_note=draft.category_structure_note, categories=categories,
            risks=risks, note=f"{verified} of {len(risks)} quotes verified against the PDF text")

    def _taxonomy_text(self) -> str:
        return "\n".join(f"- {c['key']}: {c['name']}. {c['description']}"
                         for c in self.setup.taxonomy)

    def _map(self, context_risks: list[ContextRisk], extractions: list[Extraction],
             companies: list[Company]) -> list[Theme]:
        s = self.setup
        names = {c.id: c.name for c in companies}
        risk_lines, cat_lines = [], []
        for e in extractions:
            if e.risks:
                risk_lines.append(f"## {names[e.company_id]} ({e.company_id})")
                risk_lines += [f"{r.id}: {r.title}. {r.summary} (company category: "
                               f"{r.company_category or 'none'})" for r in e.risks]
            cat_lines += [f"{k.id} ({names[e.company_id]}): {k.name}"
                          + (f" [sub-categories: {', '.join(k.sub_categories)}]"
                             if k.sub_categories else "") for k in e.categories]
        draft = self.llm.structured(
            stage="mapping", prompt_name=s.prompt_version("mapping"),
            prompt=s.prompt(
                "mapping", taxonomy=self._taxonomy_text(),
                context_risks="\n".join(f"{c.id}: {c.title} [{c.category}]"
                                        for c in context_risks) or "(none)",
                disclosed_risks="\n".join(risk_lines) or "(none)",
                categories="\n".join(cat_lines) or "(none)"),
            schema=MappingDraft, effort=s.effort("mapping"))

        themes: dict[str, Theme] = {}
        for t in draft.themes:
            key = _slug(t.key) or _slug(t.name)
            if key and key not in themes:
                category = t.category if t.category in s.taxonomy_keys else "other"
                themes[key] = Theme(key=key, name=t.name, category=category,
                                    description=t.description)
        assigned = {a.item_id: _slug(a.theme_key) for a in draft.assignments
                    if _slug(a.theme_key) in themes}

        def theme_for(item_id: str) -> str:
            key = assigned.get(item_id)
            if key is None:
                themes.setdefault(UNMAPPED.key, UNMAPPED.model_copy())
                return UNMAPPED.key
            return key

        for c in context_risks:
            c.theme_key = theme_for(c.id)
        for e in extractions:
            for r in e.risks:
                r.theme_key = theme_for(r.id)
        cat_maps = {m.category_id: m for m in draft.category_maps}
        for e in extractions:
            for k in e.categories:
                m = cat_maps.get(k.id)
                k.taxonomy_key = (m.taxonomy_key if m and m.taxonomy_key in s.taxonomy_keys
                                  else "other")
                k.mapping_note = m.note if m else "not mapped"
        return list(themes.values())

    # ------------------------------------------------------------------
    # Checkpoint 2 · Review findings

    def findings(self) -> Findings:
        companies = self.companies()
        extractions = self.store.load("extractions", list[Extraction])
        context_risks = self.store.load("context_risks", list[ContextRisk])
        themes = self.store.load("themes", list[Theme])
        risks = [r for e in extractions for r in e.risks]
        rows = build_matrix(self.setup, themes, risks, context_risks, companies)
        disclosed = client_has_disclosure(risks)
        return Findings(
            companies=companies, rows=rows, gaps=find_gaps(rows, companies, disclosed),
            structure=compare_structures(self.setup, extractions, companies),
            client_disclosed=disclosed, context_risks=context_risks, extractions=extractions,
            themes=themes, remarks=self.store.load("remarks", list[str]))

    def _edit_themes(self, keys: list[str], action: str, apply: Callable[[Theme], None],
                     value: object = None) -> list[Theme]:
        self._require_status("awaiting_findings", "exported")
        themes = self.store.load("themes", list[Theme])
        wanted = {_slug(k) for k in keys}
        changed = []
        for t in themes:
            if t.key in wanted or _slug(t.name) in wanted:
                before = t.model_dump()
                apply(t)
                self.store.log_review(action, t.key, before=before, after=t.model_dump())
                changed.append(t)
        self.store.save("themes", themes)
        self._mark_findings_changed()
        return changed

    def remove_themes(self, keys: list[str]) -> list[Theme]:
        return self._edit_themes(keys, "remove_theme", lambda t: setattr(t, "removed", True))

    def restore_themes(self, keys: list[str]) -> list[Theme]:
        return self._edit_themes(keys, "restore_theme", lambda t: setattr(t, "removed", False))

    def rename_theme(self, key: str, name: str) -> list[Theme]:
        return self._edit_themes([key], "rename_theme", lambda t: setattr(t, "name", name))

    def recategorise_theme(self, key: str, category: str) -> list[Theme]:
        if category not in self.setup.taxonomy_keys:
            raise ScopeError(f"'{category}' is not a taxonomy category "
                             f"({', '.join(self.setup.taxonomy_keys)}).")
        return self._edit_themes([key], "recategorise_theme",
                                 lambda t: setattr(t, "category", category))

    def reject_evidence(self, risk_ids: list[str], rejected: bool = True) -> list[DisclosedRisk]:
        """Reject (or, with rejected=False, restore) disclosed-risk quotes."""
        self._require_status("awaiting_findings", "exported")
        extractions = self.store.load("extractions", list[Extraction])
        wanted = {r.strip().upper() for r in risk_ids}
        changed = []
        for e in extractions:
            for r in e.risks:
                if r.id.upper() in wanted and r.rejected != rejected:
                    r.rejected = rejected
                    self.store.log_review("reject_evidence" if rejected else "restore_evidence",
                                          r.id, before=not rejected, after=rejected)
                    changed.append(r)
        self.store.save("extractions", extractions)
        self._mark_findings_changed()
        return changed

    def add_remark(self, text: str) -> None:
        self._require_status("awaiting_findings", "exported")
        remarks = self.store.load("remarks", list[str])
        remarks.append(text)
        self.store.save("remarks", remarks)
        self.store.log_review("add_remark", "category_structure", after=text)

    def _mark_findings_changed(self) -> None:
        if self.store.status == "exported":
            self._set("awaiting_findings")

    def export(self) -> Path:
        from .export import write_workbook

        self._require_status("awaiting_findings", "exported")
        path = write_workbook(self)
        self.store.log_review("export", path.name)
        self._set("exported")
        return path


def short_exchange(text: str | None) -> str | None:
    """'Singapore Exchange (SGX)' -> 'SGX'."""
    if not text:
        return None
    code = re.search(r"\(([A-Z]{2,6})\)", text)
    return code.group(1) if code else text.strip()


def iso_date(text: str | None) -> str | None:
    """The first date in the text as YYYY-MM-DD, or None."""
    if not text:
        return None
    found = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if found:
        return found.group(0)
    found = re.search(r"\b(\d{1,2}) ([A-Z][a-z]{2,8}) (\d{4})\b", text)
    if found:
        for fmt in ("%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(found.group(0), fmt).date().isoformat()
            except ValueError:
                continue
    return None


def tidy_figures(fields: dict) -> dict:
    """Keep the short display fields short; anything cut from a date goes into data_notes."""
    notes = [fields.get("data_notes")] if fields.get("data_notes") else []
    fields["exchange"] = short_exchange(fields.get("exchange"))
    raw_date = fields.get("market_cap_as_of")
    fields["market_cap_as_of"] = iso_date(raw_date)
    if raw_date and raw_date.strip() != fields["market_cap_as_of"]:
        notes.append(f"Market cap date: {raw_date.strip()}")
    if fields.get("headquarters"):
        fields["headquarters"] = fields["headquarters"].split("(")[0].strip(" ,;") or None
    fields["data_notes"] = " · ".join(notes) or None
    return fields


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")


def _name_key(name: str) -> str:
    return docs.core_name(name) if name else ""


def format_money(millions: float | None) -> str:
    if millions is None:
        return "not found"
    return f"S${millions / 1000:,.1f}B" if millions >= 1000 else f"S${millions:,.0f}M"
