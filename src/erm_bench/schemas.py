"""Data shapes for a run.

Two kinds of model live here:

- *Draft* models are what the LLM returns through structured outputs. They have
  no defaults (every field is required, nullable where needed) so the JSON
  schema the API enforces matches what the code expects.
- Run-state models are what the pipeline stores in the run folder. They hold
  resolved sources, verification results and human decisions.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Shared


class Source(BaseModel):
    """A web source the API cited during research, with the text it cited."""

    url: str
    title: str = ""
    cited_text: str = ""


BucketKey = Literal["large", "mid", "small"]

# Formats for figures shown in small tiles. Caveats go in data_notes, not in the figure.
EXCHANGE = Field(description="Exchange code only, e.g. SGX, HKEX, NYSE. Null if unlisted.")
AS_OF = Field(description="Date of the market cap figure, YYYY-MM-DD only. Null if unknown.")
PLACE = Field(description="City and country only, e.g. 'Singapore' or 'Hong Kong, China'.")
FIGURE = Field(description="Short figure with currency and period, at most 25 characters, e.g. "
                           "'S$27.0B (FY2025)' or 'HK$19.7B (FY2025)'. Caveats go in data_notes.")
PEOPLE = Field(description="Head count with year, e.g. '9,546 (2025)'. Caveats go in data_notes.")
NOTES = Field(description="Caveats about the figures: sources disagree, unverified, different "
                          "bases (e.g. statutory vs headline), currency conversions. Null if none.")


# ---------------------------------------------------------------------------
# 1 · Request


class RequestDraft(BaseModel):
    client_name: str | None
    reporting_year: int | None
    sector: str | None
    focus_regions: list[str]
    notes: str | None
    peer_count: int | None
    market_cap_bucket: BucketKey | None
    include: list[str]
    exclude: list[str]
    missing: list[str]


class Request(BaseModel):
    client_name: str
    reporting_year: int | None = None
    sector: str | None = None
    focus_regions: list[str] = Field(default_factory=list)
    notes: str = ""
    peer_count: int = 3
    market_cap_bucket: BucketKey | None = None
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    products: list[Literal["A", "B"]] = Field(default_factory=lambda: ["A"])

    def context_line(self) -> str:
        parts = [f"sector: {self.sector}" if self.sector else "",
                 f"focus regions: {', '.join(self.focus_regions)}" if self.focus_regions else "",
                 self.notes]
        return "; ".join(p for p in parts if p) or "none given"


# ---------------------------------------------------------------------------
# 2 · Client profile

FactField = Literal[
    "identity", "listing", "market_cap", "headquarters", "segments", "geography",
    "scale", "ownership", "strategy", "disclosure", "other",
]


class DraftFact(BaseModel):
    field: FactField
    text: str
    citation_ids: list[str]


class ProfileDraft(BaseModel):
    legal_name: str
    short_name: str
    listed: bool
    exchange: str | None = EXCHANGE
    ticker: str | None
    market_cap_sgd_millions: float | None
    market_cap_as_of: str | None = AS_OF
    headquarters: str | None = PLACE
    sector: str
    business_description: str = Field(description="Two sentences at most.")
    segments: list[str]
    countries: list[str]
    main_markets: list[str]
    total_assets: str | None = FIGURE
    revenue: str | None = FIGURE
    ebitda: str | None = FIGURE
    employees: str | None = PEOPLE
    parent: str | None = Field(description="Name only, with stake if known, e.g. 'Hong Leong "
                                           "Investment Holdings (49%)'.")
    data_notes: str | None = NOTES
    related_listed_entities: list[str]
    latest_annual_report_year: int | None
    facts: list[DraftFact]


class Fact(BaseModel):
    id: str
    field: str
    text: str
    sources: list[Source] = Field(default_factory=list)

    @property
    def sourced(self) -> bool:
        return bool(self.sources)


class Profile(BaseModel):
    legal_name: str
    short_name: str
    listed: bool
    exchange: str | None = None
    ticker: str | None = None
    market_cap_sgd_millions: float | None = None
    market_cap_as_of: str | None = None
    bucket: BucketKey | None = None
    headquarters: str | None = None
    sector: str = ""
    sector_guide: str = "general"
    business_description: str = ""
    segments: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    main_markets: list[str] = Field(default_factory=list)
    total_assets: str | None = None
    revenue: str | None = None
    ebitda: str | None = None
    employees: str | None = None
    parent: str | None = None
    related_listed_entities: list[str] = Field(default_factory=list)
    latest_annual_report_year: int | None = None
    data_notes: str | None = None
    facts: list[Fact] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def listing_label(self) -> str:
        if not self.listed:
            return "unlisted"
        return f"{self.exchange or '?'}: {self.ticker or '?'}"


# ---------------------------------------------------------------------------
# 2-3 · Peers


class PeerCandidateDraft(BaseModel):
    name: str
    legal_name: str
    listed: bool
    exchange: str | None = EXCHANGE
    ticker: str | None
    market_cap_sgd_millions: float | None
    market_cap_as_of: str | None = AS_OF
    headquarters: str | None = PLACE
    segments: list[str]
    operating_countries: str | None = Field(description="A count or range, e.g. '6-10'.")
    main_markets: list[str]
    total_assets: str | None = FIGURE
    revenue: str | None = FIGURE
    ebitda: str | None = FIGURE
    employees: str | None = PEOPLE
    data_notes: str | None = NOTES
    related_entity_warning: str | None
    rationale: str
    key_limitation: str
    recommended: bool
    citation_ids: list[str]


class PeerScanDraft(BaseModel):
    candidates: list[PeerCandidateDraft]
    scan_notes: str


class PeerCandidate(BaseModel):
    id: str
    name: str
    legal_name: str
    listed: bool
    exchange: str | None = None
    ticker: str | None = None
    market_cap_sgd_millions: float | None = None
    market_cap_as_of: str | None = None
    bucket: BucketKey | None = None
    headquarters: str | None = None
    segments: list[str] = Field(default_factory=list)
    operating_countries: str | None = None
    main_markets: list[str] = Field(default_factory=list)
    total_assets: str | None = None
    revenue: str | None = None
    ebitda: str | None = None
    employees: str | None = None
    related_entity_warning: str | None = None
    data_notes: str | None = None
    rationale: str = ""
    key_limitation: str = ""
    recommended: bool = False
    selected: bool = False
    added_by: Literal["ai", "consultant"] = "ai"
    sources: list[Source] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def listing_label(self) -> str:
        if not self.listed:
            return "unlisted"
        return f"{self.exchange or '?'}: {self.ticker or '?'}"


class PeerSet(BaseModel):
    bucket_filter: BucketKey | None = None
    peer_count: int = 3
    scan_notes: str = ""
    candidates: list[PeerCandidate] = Field(default_factory=list)

    def get(self, peer_id: str) -> PeerCandidate | None:
        return next((c for c in self.candidates if c.id == peer_id), None)

    @property
    def selected(self) -> list[PeerCandidate]:
        return [c for c in self.candidates if c.selected]


# ---------------------------------------------------------------------------
# 2-4 · Documents

DocType = Literal["annual_report", "sustainability_report", "corporate_governance_report", "other"]


class DocumentLinkDraft(BaseModel):
    doc_type: DocType
    title: str = Field(description="The document's title as published, without notes.")
    fiscal_year: int | None
    url: str


class DisclosureDraft(BaseModel):
    documents: list[DocumentLinkDraft]
    notes: str


DocStatus = Literal[
    "found",            # located by the scan, not downloaded yet
    "ok",               # downloaded, checks passed
    "check_failed",     # downloaded, but company / year / risk-section check failed
    "download_failed",
    "not_pdf",
    "uploaded",         # supplied by the consultant
    "na",               # marked not available by the consultant
]


class DocChecks(BaseModel):
    company_match: bool | None = None
    year_found: int | None = None
    year_match: bool | None = None
    has_risk_section: bool | None = None
    low_text_pages: int = 0


class Document(BaseModel):
    id: str
    company_id: str               # "client" or a peer id
    doc_type: DocType
    title: str
    fiscal_year: int | None = None
    url: str | None = None
    status: DocStatus = "found"
    sha256: str | None = None
    page_count: int | None = None
    checks: DocChecks = Field(default_factory=DocChecks)
    risk_pages: list[int] = Field(default_factory=list)   # 1-indexed
    note: str = ""
    previous_status: DocStatus | None = None   # before it was marked n/a, for Undo
    previous_note: str = ""


Availability = Literal["good", "partial", "none"]


# ---------------------------------------------------------------------------
# A1 · Context-inferred risks


class ContextRiskDraft(BaseModel):
    title: str
    category: str
    driver_fact_ids: list[str]
    driver: str
    consequence: str
    affected: str


class ContextRisksDraft(BaseModel):
    risks: list[ContextRiskDraft]


class ContextRisk(BaseModel):
    id: str
    title: str
    category: str
    driver_fact_ids: list[str]
    driver: str
    consequence: str
    affected: str
    theme_key: str | None = None
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# A2 · Disclosed risks and category structure


class CategoryDraft(BaseModel):
    name: str
    sub_categories: list[str]
    quote: str
    page: int


class DisclosedRiskDraft(BaseModel):
    title: str
    summary: str
    company_category: str | None
    quote: str
    page: int


class ExtractionDraft(BaseModel):
    has_risk_disclosure: bool
    categories: list[CategoryDraft]
    category_structure_note: str
    risks: list[DisclosedRiskDraft]


class Verification(BaseModel):
    verified: bool
    score: float
    found_page: int | None = None
    note: str = ""


class CompanyCategory(BaseModel):
    id: str
    company_id: str
    doc_id: str
    name: str
    sub_categories: list[str] = Field(default_factory=list)
    quote: str
    page: int
    verification: Verification
    taxonomy_key: str | None = None
    mapping_note: str = ""


class DisclosedRisk(BaseModel):
    id: str
    company_id: str
    doc_id: str
    title: str
    summary: str
    company_category: str | None = None
    quote: str
    page: int
    verification: Verification
    theme_key: str | None = None
    rejected: bool = False

    @property
    def counts(self) -> bool:
        return self.verification.verified and not self.rejected


class Extraction(BaseModel):
    company_id: str
    doc_id: str | None
    has_risk_disclosure: bool
    category_structure_note: str = ""
    categories: list[CompanyCategory] = Field(default_factory=list)
    risks: list[DisclosedRisk] = Field(default_factory=list)
    note: str = ""


# ---------------------------------------------------------------------------
# A4 · Mapping


class ThemeDraft(BaseModel):
    key: str
    name: str
    category: str
    description: str


class AssignmentDraft(BaseModel):
    item_id: str
    theme_key: str


class CategoryMapDraft(BaseModel):
    category_id: str
    taxonomy_key: str
    note: str


class MappingDraft(BaseModel):
    themes: list[ThemeDraft]
    assignments: list[AssignmentDraft]
    category_maps: list[CategoryMapDraft]


class Theme(BaseModel):
    key: str
    name: str
    category: str
    description: str = ""
    removed: bool = False


# ---------------------------------------------------------------------------
# Chat commands


class ScopeCommand(BaseModel):
    action: Literal[
        "select", "deselect", "add_peer", "set_bucket", "set_peer_count",
        "add_document", "mark_na", "lock", "show", "answer",
    ]
    targets: list[str]
    value: str | None


class ScopeCommands(BaseModel):
    commands: list[ScopeCommand]
    reply: str


class FindingsCommand(BaseModel):
    action: Literal[
        "remove_theme", "restore_theme", "rename_theme", "recategorise_theme",
        "reject_evidence", "add_remark", "export", "show", "answer",
    ]
    targets: list[str]
    value: str | None


class FindingsCommands(BaseModel):
    commands: list[FindingsCommand]
    reply: str
