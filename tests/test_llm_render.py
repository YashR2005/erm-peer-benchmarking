from types import SimpleNamespace as NS

from erm_bench.llm import render_research


def cite(url, text):
    return NS(type="web_search_result_location", url=url, title="t", cited_text=text)


def test_render_research_markers_and_sources():
    blocks = [
        NS(type="server_tool_use"),
        NS(type="text", text="CDL is listed on SGX.", citations=[cite("https://a", "listed on SGX")]),
        NS(type="text", text=" Market cap S$6B.", citations=[cite("https://b", "S$6.0B"),
                                                              cite("https://a", "listed on SGX")]),
        NS(type="web_fetch_tool_result", content=NS(url="https://ir.test/ar.pdf")),
        NS(type="text", text=" No citation here.", citations=None),
    ]
    r = render_research(blocks)
    assert r.text.startswith("CDL is listed on SGX. [c1] Market cap S$6B. [c2][c1]")
    assert "(fetched https://ir.test/ar.pdf [c3])" in r.text
    assert [s.url for s in r.citations] == ["https://a", "https://b", "https://ir.test/ar.pdf"]
    assert '[c2] https://b — "S$6.0B"' in r.citations_block()
    assert "[c3] https://ir.test/ar.pdf — (page fetched)" in r.citations_block()


def test_resolve_ignores_unknown_ids():
    r = render_research([NS(type="text", text="x", citations=[cite("https://a", "x")])])
    assert [s.url for s in r.resolve(["c1", "[c1]", "c9", "nonsense"])] == ["https://a"]


def test_search_results_become_sources_when_the_answer_has_no_citations():
    blocks = [
        NS(type="web_search_tool_result", content=[NS(url="https://a", title="A"),
                                                   NS(url="https://b", title="B")]),
        NS(type="web_search_tool_result", content=NS(error_code="max_uses_exceeded")),
        NS(type="text", text="CDL trades as C09.", citations=None),
    ]
    r = render_research(blocks)
    assert r.text == "CDL trades as C09.\n\nSearch results consulted: [c1][c2]"
    assert [(c.url, c.title) for c in r.citations] == [("https://a", "A"), ("https://b", "B")]


def test_search_results_are_not_added_when_citations_exist():
    blocks = [NS(type="web_search_tool_result", content=[NS(url="https://b", title="B")]),
              NS(type="text", text="x", citations=[cite("https://a", "quoted")])]
    assert [c.url for c in render_research(blocks).citations] == ["https://a"]
