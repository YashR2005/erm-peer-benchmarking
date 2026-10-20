"""What the consultant sees at each checkpoint, as text plus tables. Used by the chat and CLI."""

from __future__ import annotations

from dataclasses import dataclass, field

from .pipeline import CLIENT, Findings, Pipeline, format_money
from .schemas import Document

NOT_EVIDENT = "—"


@dataclass
class Reply:
    text: str
    table: list[dict] | None = None
    caption: str | None = None
    file: str | None = None
    notes: list[str] = field(default_factory=list)


def _bucket_label(p: Pipeline, key: str | None) -> str:
    return p.setup.bucket(key).label if key else "unknown"


def _doc_cell(d: Document | None) -> str:
    if d is None:
        return "none found"
    year = f" FY{d.fiscal_year}" if d.fiscal_year else ""
    return f"{d.id}{year} ({d.status})"


# ---------------------------------------------------------------------------
# Checkpoint 1


def profile_reply(p: Pipeline) -> Reply:
    pr = p.profile
    sourced = sum(1 for f in pr.facts if f.sourced)
    lines = [
        f"**{pr.legal_name}** · {pr.listing_label()} · market cap {format_money(pr.market_cap_sgd_millions)}"
        f" ({_bucket_label(p, pr.bucket)}{', as of ' + pr.market_cap_as_of if pr.market_cap_as_of else ''})",
        f"{pr.business_description}",
        f"- Segments: {', '.join(pr.segments) or 'not found'}",
        f"- Countries: {', '.join(pr.countries) or 'not found'}; main markets: "
        f"{', '.join(pr.main_markets) or 'not found'}",
        f"- Scale: total assets {pr.total_assets or 'n/f'}, revenue {pr.revenue or 'n/f'}, "
        f"EBITDA {pr.ebitda or 'n/f'}, employees {pr.employees or 'n/f'}",
        f"- Sector guide used: {pr.sector_guide}",
        f"- {sourced} of {len(pr.facts)} profile facts have a cited source.",
    ]
    lines += [f"- ⚠️ {w}" for w in pr.warnings]
    client_doc = p.primary_document(CLIENT)
    lines.append(f"\n**Client disclosure:** {p.availability(CLIENT)} · main document: "
                 f"{_doc_cell(client_doc)}")
    if p.availability(CLIENT) != "good":
        lines.append("The client has little usable public disclosure. You can continue with "
                     "'insufficient evidence' in its column (peer reference only), or add a "
                     "document: `add document client <PDF URL>`.")
    return Reply(text="\n".join(lines))


def peers_reply(p: Pipeline) -> Reply:
    peers = p.peers
    rows = []
    for c in peers.candidates:
        doc = p.primary_document(c.id)
        rows.append({
            "Use": "✓" if c.selected else "",
            "ID": c.id,
            "Peer": c.name,
            "Listing": c.listing_label(),
            "Mkt cap": format_money(c.market_cap_sgd_millions),
            "Bucket": _bucket_label(p, c.bucket),
            "HQ": c.headquarters or "",
            "Segments": ", ".join(c.segments),
            "Countries": c.operating_countries or "",
            "Markets": ", ".join(c.main_markets),
            "Assets": c.total_assets or "",
            "Revenue": c.revenue or "",
            "EBITDA": c.ebitda or "",
            "Employees": c.employees or "",
            "Annual report": _doc_cell(doc),
            "Risk section": "yes" if doc and doc.checks.has_risk_section else "no",
            "Disclosure": p.availability(c.id),
            "AI pick": "recommended" if c.recommended else ("added" if c.added_by == "consultant"
                                                            else "alternative"),
            "Why comparable": c.rationale,
            "Limitation": c.key_limitation,
            "Warnings": "; ".join(c.warnings),
        })
    bucket = (f"Market-cap filter: {_bucket_label(p, peers.bucket_filter)}"
              if peers.bucket_filter else "Market-cap filter: none (all buckets)")
    text = (f"**Peer candidates** · {len(peers.selected)} selected of {len(peers.candidates)} · "
            f"{bucket}.\n{peers.scan_notes}")
    return Reply(text=text, table=rows, caption="Peer candidates (columns as in Marsh's Appendix A)")


def sources_reply(p: Pipeline) -> Reply:
    ids = {CLIENT} | {c.id for c in p.peers.selected}
    rows = []
    for d in p.documents:
        if d.company_id not in ids:
            continue
        rows.append({
            "Doc": d.id,
            "Company": p.company_name(d.company_id),
            "Type": d.doc_type.replace("_", " "),
            "Title": d.title,
            "FY": d.fiscal_year or "",
            "Status": d.status,
            "Pages": d.page_count or "",
            "Risk pages": len(d.risk_pages) or "",
            "Note": d.note,
            "URL": d.url or "",
        })
    return Reply(text="**Sources** for the client and selected peers. Fix a source with "
                      "`add document <company id> <PDF URL>` or `mark <doc id> n/a <reason>`.",
                 table=rows, caption="Sources")


def scope_replies(p: Pipeline) -> list[Reply]:
    return [
        profile_reply(p), peers_reply(p), sources_reply(p),
        Reply(text="Tell me what to change, for example *drop P4*, *add UOL Group*, "
                   "*only mid caps*, *use 4 peers*. When the peer set and sources look right, "
                   "say **go** to lock the scope and run the analysis."),
    ]


# ---------------------------------------------------------------------------
# Checkpoint 2


def _cell(row, company_id: str, client_disclosed: bool) -> str:
    if company_id == CLIENT and not client_disclosed:
        return "insufficient evidence"
    risks = row.cells.get(company_id) or []
    return "✓ " + ", ".join(r.id for r in risks) if risks else NOT_EVIDENT


def matrix_rows(p: Pipeline, f: Findings) -> list[dict]:
    rows = []
    for row in f.rows:
        record = {
            "Category": p.setup.taxonomy_name(row.theme.category),
            "Theme": row.theme.name,
            "Key": row.theme.key,
            "Context-inferred": ", ".join(row.context_risk_ids),
        }
        for c in f.companies:
            record[c.name] = _cell(row, c.id, f.client_disclosed)
        rows.append(record)
    return rows


def structure_rows(p: Pipeline, f: Findings) -> list[dict]:
    rows = []
    for key in [*p.setup.taxonomy_keys, "other"]:
        record = {"Standard category": p.setup.taxonomy_name(key)}
        for c in f.companies:
            record[c.name] = ", ".join(f.structure.by_taxonomy[key][c.id]) or NOT_EVIDENT
        if any(v != NOT_EVIDENT for k, v in record.items() if k != "Standard category"):
            rows.append(record)
    count = {"Standard category": "Number of categories"}
    for col in f.structure.columns:
        count[col.company.name] = str(len(col.categories)) if col.has_structure else "none found"
    return [count] + rows


def findings_replies(p: Pipeline) -> list[Reply]:
    f = p.findings()
    risks = f.risks
    verified = sum(1 for r in risks if r.verification.verified)
    rejected = sum(1 for r in risks if r.rejected)
    header = [
        f"**Findings** for {', '.join(c.name for c in f.companies)}.",
        f"- {len(risks)} disclosed risks extracted; {verified} quotes verified against the PDF "
        f"text, {len(risks) - verified} could not be verified and are left out of the matrix"
        + (f"; {rejected} rejected by you." if rejected else "."),
        f"- {len(f.context_risks)} context-inferred risks for the client (ids C1…).",
    ]
    for e in f.extractions:
        if not e.has_risk_disclosure or not e.risks:
            header.append(f"- {p.company_name(e.company_id)}: {e.note or 'no risk disclosure found'}")
    if not f.client_disclosed:
        header.append("- The client has no verified risk disclosure of its own, so its column "
                      "reads 'insufficient evidence' and no client gaps are reported.")
    replies = [Reply(text="\n".join(header))]
    replies.append(Reply(
        text="**Risk coverage matrix.** ✓ = disclosed (risk ids), — = not publicly evident. "
             "Not publicly evident does not mean the risk is unmanaged.",
        table=matrix_rows(p, f), caption="Risk coverage"))
    if f.gaps:
        replies.append(Reply(
            text="**Gaps** (only where the client has its own disclosure).",
            table=[{"Gap": g.label, "Theme": g.theme.name,
                    "Category": p.setup.taxonomy_name(g.theme.category), "Detail": g.detail}
                   for g in f.gaps], caption="Gaps"))
    structure_text = ["**Risk category structure** (how each company groups its risks)."]
    structure_text += [f"- {o}" for o in f.structure.observations]
    if f.remarks:
        structure_text.append("\nYour remarks:")
        structure_text += [f"- {r}" for r in f.remarks]
    replies.append(Reply(text="\n".join(structure_text), table=structure_rows(p, f),
                         caption="Category structure"))
    replies.append(Reply(
        text="Tell me what to change, for example *remove theme <key>*, *rename <key> to …*, "
             "*move <key> to financial*, *reject R-P1-3*, *remark: …*, or *evidence for <key>*. "
             "Say **export** for the Excel workbook."))
    return replies


def evidence_reply(p: Pipeline, theme_keys: list[str]) -> Reply:
    f = p.findings()
    wanted = set(theme_keys)
    names = {c.id: c.name for c in f.companies}
    rows = [{
        "Risk": r.id, "Company": names.get(r.company_id, r.company_id), "Theme": r.theme_key,
        "Company wording": r.title, "Quote": r.quote, "Page": r.page,
        "Verified": "yes" if r.verification.verified else "no",
        "Rejected": "yes" if r.rejected else "",
    } for r in f.risks if r.theme_key in wanted]
    ctx = [c for c in f.context_risks if c.theme_key in wanted]
    text = "**Evidence**" + (": " + "; ".join(f"{c.id} {c.title} (driver: {c.driver})"
                                                for c in ctx) if ctx else "")
    return Reply(text=text, table=rows or None, caption="Evidence")
