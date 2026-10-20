"""A fake LLM and fake documents: one client and three peers, end to end, offline."""

from __future__ import annotations

from erm_bench.documents import DownloadError
from erm_bench.llm import ResearchResult
from erm_bench.schemas import (
    CategoryDraft,
    ContextRiskDraft,
    ContextRisksDraft,
    DisclosedRiskDraft,
    DisclosureDraft,
    DocumentLinkDraft,
    DraftFact,
    ExtractionDraft,
    FindingsCommand,
    FindingsCommands,
    MappingDraft,
    PeerCandidateDraft,
    PeerScanDraft,
    ProfileDraft,
    RequestDraft,
    ScopeCommand,
    ScopeCommands,
    Source,
)

from .conftest import make_pdf

# Documents ---------------------------------------------------------------

CLIENT_PAGES = [
    "City Developments Limited\nAnnual Report 2025",
    "Contents\nChairman's statement\nRisk Management",
    "Chairman's statement\nWe delivered a resilient performance.",
    "Risk Management\nOur risks are grouped into four categories: Strategic, Financial, "
    "Operational and Compliance.\nInterest rate risk\nRising interest rates may increase the "
    "Group's financing costs on its borrowings.\nProperty market risk\nA downturn in residential "
    "prices in Singapore could reduce development margins.",
    "Risk Management (continued)\nCybersecurity risk\nA cyber attack could disrupt the Group's IT "
    "systems and expose personal data. The risk owner monitors mitigation actions.",
    "Financial statements\nRevenue for the year was S$3.1 billion.",
]
CLIENT_TOC = [[1, "Chairman's statement", 3], [1, "Risk Management", 4],
              [1, "Financial Statements", 6]]

ALPHA_PAGES = [
    "Alpha Land Holdings Limited\nAnnual Report 2025",
    "Enterprise Risk Management\nWe group our risks into Strategic, Financial, Operational, "
    "Compliance and Safety & Security risks.\nInterest rate risk: higher rates raise the cost "
    "of our bank loans.",
    "Key risks (continued)\nConstruction delays: delays by main contractors may defer the "
    "completion of our development projects. Workplace safety: a serious accident on a "
    "construction site could harm workers. Mitigation is tracked in the risk register.",
]

BETA_PAGES = [
    "Beta Properties Ltd\nAnnual Report 2025",
    "Our portfolio\nWe own offices in Singapore.",
    "Key Risks\nRising interest rates increase our refinancing costs. Climate change may "
    "cause flooding at coastal assets. Mitigation and risk owner reviews are in place.",
]

DOCS = {
    "https://ir.test/client.pdf": make_pdf(CLIENT_PAGES, CLIENT_TOC),
    "https://ir.test/alpha.pdf": make_pdf(ALPHA_PAGES),
    "https://ir.test/beta.pdf": make_pdf(BETA_PAGES),
    "https://ir.test/gamma.html": b"<html>Gamma REIT investor relations</html>",
    # An SGX-style announcement page that links to its attachments.
    "https://sgx.test/ann/1": b'<html><a href="/ann/1/2_Letter_to_Unitholders.pdf">Letter</a>'
                             b'<a href="/ann/1/1_Gamma_REIT_Annual_Report_2025.pdf">AR</a></html>',
    "https://sgx.test/ann/1/2_Letter_to_Unitholders.pdf": make_pdf(["Letter to unitholders"]),
    "https://sgx.test/ann/1/1_Gamma_REIT_Annual_Report_2025.pdf": make_pdf(
        ["Gamma REIT\nAnnual Report 2025", "Risk Management\nKey risks and mitigation."]),
}


def fake_fetch(url: str) -> bytes:
    if url not in DOCS:
        raise DownloadError("HTTP 404")
    return DOCS[url]


# LLM ---------------------------------------------------------------------

def _candidate(name, legal, cap, recommended):
    return PeerCandidateDraft(
        name=name, legal_name=legal, listed=True, exchange="SGX", ticker=name[:3].upper(),
        market_cap_sgd_millions=cap, market_cap_as_of="2026-10-01", headquarters="Singapore",
        segments=["Property development"], operating_countries="1-5", main_markets=["Singapore"],
        total_assets="S$5B (FY2025)", revenue="S$1B (FY2025)", ebitda=None, employees="1,000",
        data_notes=None, related_entity_warning=None, rationale=f"{name} develops property in Singapore.",
        key_limitation="Smaller overseas portfolio.", recommended=recommended,
        citation_ids=["c1"])


DOC_URLS = {"CDL": "https://ir.test/client.pdf", "Alpha Land": "https://ir.test/alpha.pdf",
            "Beta Properties": "https://ir.test/beta.pdf", "Gamma REIT": "https://ir.test/gamma.html"}

EXTRACTIONS = {
    "client": ExtractionDraft(
        has_risk_disclosure=True,
        categories=[CategoryDraft(name=n, sub_categories=[], page=4,
                                  quote="Our risks are grouped into four categories: Strategic, "
                                        "Financial, Operational and Compliance.")
                    for n in ["Strategic", "Financial", "Operational", "Compliance"]],
        category_structure_note="Four categories listed in the risk management section.",
        risks=[
            DisclosedRiskDraft(title="Interest rate risk", summary="Financing costs may rise.",
                               company_category="Financial", page=4,
                               quote="Rising interest rates may increase the Group's financing "
                                     "costs on its borrowings."),
            DisclosedRiskDraft(title="Property market risk", summary="Prices may fall.",
                               company_category="Strategic", page=4,
                               quote="A downturn in residential prices in Singapore could reduce "
                                     "development margins."),
            DisclosedRiskDraft(title="Cybersecurity risk", summary="Cyber attack.",
                               company_category="Operational", page=5,
                               quote="A cyber attack could disrupt the Group's IT systems and "
                                     "expose personal data."),
            # Invented: must fail verification and stay out of the matrix.
            DisclosedRiskDraft(title="Bribery risk", summary="Zero tolerance.",
                               company_category="Compliance", page=4,
                               quote="The Group has zero tolerance for bribery and corruption."),
        ]),
    "P1": ExtractionDraft(
        has_risk_disclosure=True,
        categories=[CategoryDraft(name=n, sub_categories=[], page=2,
                                  quote="We group our risks into Strategic, Financial, "
                                        "Operational, Compliance and Safety & Security risks.")
                    for n in ["Strategic", "Financial", "Operational", "Compliance",
                              "Safety & Security"]],
        category_structure_note="Five categories.",
        risks=[
            DisclosedRiskDraft(title="Interest rate risk", summary="Higher loan costs.",
                               company_category="Financial", page=2,
                               quote="Interest rate risk: higher rates raise the cost of our "
                                     "bank loans."),
            # Cited on the wrong page: verified with the page corrected to 3.
            DisclosedRiskDraft(title="Construction delays", summary="Contractor delays.",
                               company_category="Operational", page=2,
                               quote="delays by main contractors may defer the completion of our "
                                     "development projects"),
            DisclosedRiskDraft(title="Workplace safety", summary="Site accidents.",
                               company_category="Safety & Security", page=3,
                               quote="a serious accident on a construction site could harm "
                                     "workers"),
        ]),
    "P2": ExtractionDraft(
        has_risk_disclosure=True, categories=[],
        category_structure_note="Risks are listed without categories.",
        risks=[
            DisclosedRiskDraft(title="Refinancing", summary="Refinancing costs.",
                               company_category=None, page=3,
                               quote="Rising interest rates increase our refinancing costs."),
            DisclosedRiskDraft(title="Climate change", summary="Flooding.", company_category=None,
                               page=3, quote="Climate change may cause flooding at coastal "
                                             "assets."),
        ]),
}

MAPPING = MappingDraft(
    themes=[
        {"key": "interest_rate", "name": "Interest rate and refinancing", "category": "financial",
         "description": "Higher borrowing costs."},
        {"key": "property_market", "name": "Property market downturn", "category": "strategic",
         "description": "Falling prices."},
        {"key": "cyber", "name": "Cybersecurity", "category": "technology",
         "description": "Cyber attacks and data breaches."},
        {"key": "construction_delay", "name": "Construction delays", "category": "operational",
         "description": "Project delays."},
        {"key": "workplace_safety", "name": "Workplace safety", "category": "people",
         "description": "Site accidents."},
        {"key": "fx", "name": "Foreign exchange", "category": "financial",
         "description": "Currency moves."},
    ],
    assignments=[
        {"item_id": "C1", "theme_key": "interest_rate"},
        {"item_id": "C2", "theme_key": "fx"},
        {"item_id": "R-client-1", "theme_key": "interest_rate"},
        {"item_id": "R-client-2", "theme_key": "property_market"},
        {"item_id": "R-client-3", "theme_key": "cyber"},
        {"item_id": "R-client-4", "theme_key": "property_market"},
        {"item_id": "R-P1-1", "theme_key": "interest_rate"},
        {"item_id": "R-P1-2", "theme_key": "construction_delay"},
        {"item_id": "R-P1-3", "theme_key": "workplace_safety"},
        {"item_id": "R-P2-1", "theme_key": "interest_rate"},
        # R-P2-2 (climate) deliberately unassigned: must land in "Not mapped (review)".
    ],
    category_maps=[
        *[{"category_id": f"K-client-{i}", "taxonomy_key": k, "note": ""} for i, k in
          enumerate(["strategic", "financial", "operational", "legal_compliance"], start=1)],
        *[{"category_id": f"K-P1-{i}", "taxonomy_key": k, "note": ""} for i, k in
          enumerate(["strategic", "financial", "operational", "legal_compliance", "other"],
                    start=1)],
    ],
)


class FakeLLM:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.scope_commands: list[ScopeCommands] = []
        self.findings_commands: list[FindingsCommands] = []
        self.extractions = dict(EXTRACTIONS)

    def research(self, *, stage, prompt_name, prompt, effort, max_searches=5, max_fetches=2):
        self.calls.append(("research", stage))
        return ResearchResult(text=f"notes for {stage} [c1]",
                              citations=[Source(url=f"https://src.test/{stage}", cited_text="x")])

    def structured(self, *, stage, prompt_name, prompt, schema, effort):
        self.calls.append(("structured", stage))
        name = schema.__name__
        if name == "RequestDraft":
            return RequestDraft(client_name="City Developments", reporting_year=2025, sector=None,
                                focus_regions=[], notes=None, peer_count=2,
                                market_cap_bucket=None, include=[], exclude=[], missing=[])
        if name == "ProfileDraft":
            return ProfileDraft(
                legal_name="City Developments Limited", short_name="CDL", listed=True,
                exchange="SGX", ticker="C09", market_cap_sgd_millions=6500,
                market_cap_as_of="2026-10-01", headquarters="Singapore",
                sector="Real estate development", business_description="Property developer.",
                segments=["Property development", "Hotel operations"],
                countries=["Singapore", "China", "UK"], main_markets=["Singapore"],
                total_assets="S$25B", revenue="S$3.1B", ebitda=None, employees="7,000",
                parent=None, data_notes=None, related_listed_entities=["CDL Hospitality Trusts"],
                latest_annual_report_year=2025,
                facts=[DraftFact(field="listing", text="Listed on SGX (C09).", citation_ids=["c1"]),
                       DraftFact(field="geography", text="Assets in China and the UK.",
                                 citation_ids=["c1"]),
                       DraftFact(field="other", text="Unsourced claim.", citation_ids=["c9"])])
        if name == "PeerScanDraft":
            return PeerScanDraft(scan_notes="Four candidates.", candidates=[
                _candidate("Alpha Land", "Alpha Land Holdings Limited", 5000, True),
                _candidate("Beta Properties", "Beta Properties Ltd", 1200, True),
                _candidate("Gamma REIT", "Gamma REIT", 2000, False),
                _candidate("City Developments", "City Developments Ltd", 6500, False),  # the client
            ])
        if name == "DisclosureDraft":
            company = next(n for n in DOC_URLS if f"documents for {n}." in prompt)
            return DisclosureDraft(notes="", documents=[DocumentLinkDraft(
                doc_type="annual_report", title="Annual Report 2025", fiscal_year=2025,
                url=DOC_URLS[company])])
        if name == "ContextRisksDraft":
            return ContextRisksDraft(risks=[
                ContextRiskDraft(title="Interest rate risk", category="financial",
                                 driver_fact_ids=["F1"], driver="Leveraged developer.",
                                 consequence="Higher costs.", affected="Group"),
                ContextRiskDraft(title="FX risk", category="financial",
                                 driver_fact_ids=["F2", "F99"], driver="Overseas assets.",
                                 consequence="Translation losses.", affected="China, UK"),
            ])
        if name == "ExtractionDraft":
            return self.extractions[stage.split(":")[1]]
        if name == "MappingDraft":
            return MAPPING
        if name == "ScopeCommands":
            return self.scope_commands.pop(0)
        if name == "FindingsCommands":
            return self.findings_commands.pop(0)
        raise AssertionError(f"unexpected schema {name}")


def scope(action, targets=(), value=None, reply="OK"):
    return ScopeCommands(commands=[ScopeCommand(action=action, targets=list(targets), value=value)],
                         reply=reply)


def findings(action, targets=(), value=None, reply="OK"):
    return FindingsCommands(
        commands=[FindingsCommand(action=action, targets=list(targets), value=value)], reply=reply)
