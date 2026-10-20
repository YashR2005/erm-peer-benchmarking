"""Product A outputs, computed by plain code from reviewed data (v1.2 step A6).

- Risk coverage matrix: risk theme x company, which companies disclose it.
- Gaps, only where the client has its own disclosure.
- Risk category structure comparison (Client X appendix p.15).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schemas import CompanyCategory, ContextRisk, DisclosedRisk, Extraction, Theme
from .standard_setup import StandardSetup

CLIENT = "client"

GAP_LABELS = {
    "relevant_not_disclosed": "Relevant to the client, not disclosed by it",
    "disclosed_not_in_context": "Disclosed by the client, missing from the context-inferred list",
    "peers_disclose_client_silent": "Disclosed by peers, not by the client",
}


@dataclass
class Company:
    id: str
    name: str

    @property
    def is_client(self) -> bool:
        return self.id == CLIENT


@dataclass
class MatrixRow:
    theme: Theme
    context_risk_ids: list[str]
    cells: dict[str, list[DisclosedRisk]]   # company id -> verified, non-rejected risks

    def discloses(self, company_id: str) -> bool:
        return bool(self.cells.get(company_id))


@dataclass
class Gap:
    type: str
    theme: Theme
    detail: str

    @property
    def label(self) -> str:
        return GAP_LABELS[self.type]


@dataclass
class StructureColumn:
    company: Company
    has_structure: bool
    note: str
    categories: list[CompanyCategory]


@dataclass
class StructureComparison:
    columns: list[StructureColumn]
    # taxonomy key -> company id -> names of that company's categories mapped to the key
    by_taxonomy: dict[str, dict[str, list[str]]]
    observations: list[str] = field(default_factory=list)


def build_matrix(setup: StandardSetup, themes: list[Theme], risks: list[DisclosedRisk],
                 context_risks: list[ContextRisk], companies: list[Company]) -> list[MatrixRow]:
    order = {key: i for i, key in enumerate(setup.taxonomy_keys)}
    rows = []
    for theme in themes:
        if theme.removed:
            continue
        cells = {c.id: [r for r in risks if r.theme_key == theme.key and r.company_id == c.id
                        and r.counts] for c in companies}
        ctx = [c.id for c in context_risks if c.theme_key == theme.key]
        if not ctx and not any(cells.values()):
            continue
        rows.append(MatrixRow(theme=theme, context_risk_ids=ctx, cells=cells))
    rows.sort(key=lambda r: (order.get(r.theme.category, len(order)), r.theme.name.lower()))
    return rows


def client_has_disclosure(risks: list[DisclosedRisk]) -> bool:
    return any(r.company_id == CLIENT and r.counts for r in risks)


def find_gaps(rows: list[MatrixRow], companies: list[Company], client_disclosed: bool) -> list[Gap]:
    """The three gap types from the vision document. None are reported when the client has no
    disclosure of its own: its column reads "insufficient evidence" instead."""
    if not client_disclosed:
        return []
    peers = [c for c in companies if not c.is_client]
    gaps = []
    for row in rows:
        client = row.discloses(CLIENT)
        if row.context_risk_ids and not client:
            gaps.append(Gap("relevant_not_disclosed", row.theme,
                            f"context-inferred ({', '.join(row.context_risk_ids)})"))
        if client and not row.context_risk_ids:
            gaps.append(Gap("disclosed_not_in_context", row.theme, "client discloses it"))
        n = sum(1 for p in peers if row.discloses(p.id))
        if n and not client:
            gaps.append(Gap("peers_disclose_client_silent", row.theme,
                            f"{n} of {len(peers)} peers disclose it"))
    return gaps


def compare_structures(setup: StandardSetup, extractions: list[Extraction],
                       companies: list[Company]) -> StructureComparison:
    by_company = {e.company_id: e for e in extractions}
    columns = []
    by_taxonomy: dict[str, dict[str, list[str]]] = {
        key: {c.id: [] for c in companies} for key in [*setup.taxonomy_keys, "other"]
    }
    for company in companies:
        ext = by_company.get(company.id)
        cats = [k for k in (ext.categories if ext else []) if k.verification.verified]
        note = ext.category_structure_note if ext else "no document read"
        columns.append(StructureColumn(company, bool(cats), note, cats))
        for cat in cats:
            key = cat.taxonomy_key if cat.taxonomy_key in by_taxonomy else "other"
            by_taxonomy[key][company.id].append(cat.name)

    observations = []
    structured = [col for col in columns if col.has_structure]
    for col in structured:
        subs = sum(1 for c in col.categories if c.sub_categories)
        detail = f", {subs} with sub-categories" if subs else ""
        observations.append(f"{col.company.name} uses {len(col.categories)} risk categories{detail}.")
    for key in setup.taxonomy_keys:
        missing = [col.company.name for col in structured if not by_taxonomy[key][col.company.id]]
        if structured and len(missing) == len(structured):
            observations.append(
                f"No company has a category for {setup.taxonomy_name(key)}.")
        elif missing and len(structured) > 1:
            observations.append(
                f"{', '.join(missing)} {'has' if len(missing) == 1 else 'have'} no category for "
                f"{setup.taxonomy_name(key)}.")
    for col in structured:
        for name in by_taxonomy["other"][col.company.id]:
            observations.append(
                f"{col.company.name} has a category outside the standard taxonomy: {name}.")
    for col in columns:
        if not col.has_structure:
            note = (col.note or "no category structure found").rstrip(". ")
            observations.append(f"{col.company.name}: {note}.")
    return StructureComparison(columns=columns, by_taxonomy=by_taxonomy, observations=observations)
