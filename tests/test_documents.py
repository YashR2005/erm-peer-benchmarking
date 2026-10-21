import pytest

from erm_bench import documents as docs

from .conftest import make_pdf


def test_core_name_strips_suffixes_and_brackets():
    assert docs.core_name("City Developments Limited (SGX: C09)") == "city developments"
    assert docs.core_name("UOL Group Ltd") == "uol"


def test_company_and_year_checks():
    pages = ["CITY DEVELOPMENTS LIMITED\nAnnual Report 2025", "Contents"]
    assert docs.company_matches(pages, ["City Developments Limited"])
    assert not docs.company_matches(pages, ["UOL Group Limited"])
    assert docs.find_report_year(pages) == 2025
    assert docs.year_matches(2025, 2025) and docs.year_matches(2026, 2025)
    assert docs.year_matches(2023, 2025) is False
    assert docs.year_matches(None, 2025) is None


def test_store_pdf_rejects_html(tmp_path):
    with pytest.raises(docs.NotPdfError):
        docs.store_pdf(b"<html>Investor relations</html>", tmp_path)
    stored = docs.store_pdf(make_pdf(["hello"]), tmp_path)
    assert stored.path.exists() and stored.path.stem == stored.sha256


def test_locate_by_bookmarks(tmp_path, setup):
    pdf = make_pdf(
        ["Cover", "Chairman's statement", "Risk Management\nOur risk appetite ...",
         "Key risks continued\nmitigation", "Financial statements"],
        toc=[[1, "Chairman's statement", 2], [1, "Risk Management", 3],
             [1, "Financial Statements", 5]])
    path = docs.store_pdf(pdf, tmp_path).path
    pages = docs.extract_pages(path)
    found = docs.locate_risk_sections(pages, docs.read_toc(path),
                                      setup.filing_sections["annual_report"])
    assert found.pages == [3, 4]
    assert found.has_section and found.method == "bookmarks"


def test_locate_by_page_heading(setup):
    pages = ["Cover", "Our business", "Key Risks\nInterest rate risk: mitigation through hedging. "
             "Risk owner reviews the risk register.", "Notes to the financial statements\n"
             "Financial risk management objectives: credit risk"]
    found = docs.locate_risk_sections(pages, [], setup.filing_sections["annual_report"])
    assert 3 in found.pages and 4 not in found.pages
    assert found.has_section and found.method == "headings"


RISKY = " Each risk owner reviews this risk, its risk rating and the risk response. " * 2


def test_a_page_citing_a_financial_risk_note_does_not_end_the_section(setup):
    """UOL 2025: the page after the ERM heading mentions the financial-risk note, and the risk
    list itself comes on the pages after that."""
    pages = ["Cover",
             "Enterprise Risk Management Framework\nThe Group's ERM framework." + RISKY,
             "Senior management are key risk owners. See financial risk management objectives "
             "and policies in the notes." + RISKY,
             "• Operational Risks\nSafety comes first." + RISKY,
             "• Information Technology Risks\nCyber attacks." + RISKY,
             "Directors' statement\nThe directors present their statement."]
    found = docs.locate_risk_sections(pages, [], setup.filing_sections["annual_report"])
    assert found.pages == [2, 3, 4, 5]


def test_wrapped_body_text_is_not_a_heading(setup):
    """Keppel 2025: governance pages whose lines wrap as 'risk management systems and', and a
    risk section headed 'Enterprise Risk Framework'."""
    pages = ["Cover",
             "internal controls, risk management,\nfinancial reporting and compliance, and satisfy "
             "itself as to the adequacy of such processes.",
             "1.20 a. Review the Board's\nrisk management systems and\nstate whether it concurs.",
             "ENTERPRISE RISK FRAMEWORK\nThe Board has set three risk tolerance principles." + RISKY,
             "FINANCIAL RISKS\nFinancial and Funding Risks." + RISKY,
             "Compliance\nWe take a holistic approach."]
    found = docs.locate_risk_sections(pages, [], setup.filing_sections["annual_report"])
    assert found.pages == [4, 5] and found.method == "headings"


def test_no_risk_section(setup):
    pages = ["Cover", "Our business", "Financial statements"]
    found = docs.locate_risk_sections(pages, [], setup.filing_sections["annual_report"])
    assert found.pages == [] and not found.has_section


def test_page_tagged_text():
    assert docs.page_tagged_text(["a", "b", "c"], [2]) == '<page n="2">\nb\n</page>'


def test_pdf_links_and_ranking():
    html = ('<a href="/1.0.0/ann/X/839761_2_Letter_to_Shareholders.pdf">L</a>'
            "<a href='/1.0.0/ann/X/839760_1_City_Developments_Limited_Annual_Report_2024_web.pdf'>"
            '<a href="/1.0.0/ann/X/839760_1_City_Developments_Limited_Annual_Report_2024_web.pdf">'
            '<a href="https://other.test/page.html">no</a>')
    links = docs.pdf_links(html, "https://links.sgx.com/1.0.0/ann/X")
    assert links == [
        "https://links.sgx.com/1.0.0/ann/X/839761_2_Letter_to_Shareholders.pdf",
        "https://links.sgx.com/1.0.0/ann/X/839760_1_City_Developments_Limited_Annual_Report_2024_web.pdf",
    ]
    assert docs.rank_attachments(links, "annual_report")[0].endswith("Annual_Report_2024_web.pdf")


def test_financial_statement_note_is_not_a_risk_section(setup):
    pages = ["Cover", "Notes to the financial statements\nFinancial Risk Management\n"
             "Credit risk mitigation and risk owner"]
    found = docs.locate_risk_sections(pages, [], setup.filing_sections["annual_report"])
    assert not found.has_section


def test_every_prompt_renders(setup):
    """A literal '$' in a prompt (e.g. 'S$3B') breaks string.Template; it must be written '$$'."""
    import re

    for path in sorted((setup.root / "prompts").glob("*.md")):
        names = set(re.findall(r"\$([a-z_]+)", path.read_text()))
        assert setup.prompt(path.stem, **{n: "x" for n in names})
