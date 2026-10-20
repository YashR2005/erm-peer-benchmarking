from erm_bench.verify import normalize, normalize_pages, verify_quote

PAGES = normalize_pages([
    "Cover page\nCity Developments Limited",
    "Risk Management\nRising interest rates may increase the Group’s financing\ncosts on its "
    "borrow-\nings and reduce development margins.",
    "Sustainability\nPhysical climate risks could damage our assets.",
])


def test_normalize_handles_pdf_noise():
    assert normalize("the Group’s  ﬁnancing\ncosts") == "the group's financing costs"
    assert normalize("borrow-\nings") == "borrowings"


def test_exact_quote_on_cited_page():
    v = verify_quote("Rising interest rates may increase the Group's financing costs", PAGES, 2)
    assert v.verified and v.found_page == 2 and v.note == ""


def test_quote_across_line_break_and_hyphenation():
    v = verify_quote("financing costs on its borrowings and reduce development margins", PAGES, 2)
    assert v.verified


def test_wrong_page_is_corrected():
    v = verify_quote("Physical climate risks could damage our assets.", PAGES, 1)
    assert v.verified and v.found_page == 3
    assert "not the cited page" in v.note


def test_invented_quote_fails():
    v = verify_quote("The Group has zero tolerance for regulatory breaches of any kind.", PAGES, 2)
    assert not v.verified
    assert v.note == "quote not found in the document text"


def test_paraphrase_fails():
    v = verify_quote("Higher interest rates could raise what the Group pays to borrow", PAGES, 2)
    assert not v.verified


def test_too_short_quote_fails():
    assert not verify_quote("risk", PAGES, 2).verified
