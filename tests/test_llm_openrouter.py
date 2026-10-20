from types import SimpleNamespace as NS

import pytest

from erm_bench.llm import ClaudeLLM, LLMError, LLMRefusal
from erm_bench.llm_openrouter import (
    OpenRouterLLM,
    json_text,
    render_annotated,
    strict_json_schema,
)
from erm_bench.pipeline import make_llm
from erm_bench.schemas import ProfileDraft, RequestDraft


def completion(content, annotations=None, finish="stop", refusal=None):
    message = NS(content=content, annotations=annotations, refusal=refusal)
    return NS(choices=[NS(finish_reason=finish, message=message)], id="gen-1",
              model="nvidia/nemotron-3.5-super-vl", usage=NS(model_dump=lambda: {"total_tokens": 9}))


class FakeCompletions:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def llm_with(*responses):
    completions = FakeCompletions(*responses)
    log: list[dict] = []
    llm = OpenRouterLLM("nvidia/nemotron-3.5-super-vl", "system", log=log.append,
                        client=NS(chat=NS(completions=completions)))
    return llm, completions, log


def cite(url, end=None, content="excerpt"):
    return {"type": "url_citation", "url_citation": {"url": url, "title": "t", "start_index": 0,
                                                     "end_index": end, "content": content}}


REQUEST_JSON = ('{"client_name": "CDL", "reporting_year": null, "sector": null, "focus_regions": [], '
                '"notes": null, "peer_count": null, "market_cap_bucket": null, "include": [], '
                '"exclude": [], "missing": []}')


def test_strict_schema_requires_everything():
    schema = strict_json_schema(ProfileDraft)
    objects = [schema, *schema.get("$defs", {}).values()]
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])


def test_json_text_strips_fences_and_prose():
    assert json_text('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert json_text('Here you go: {"a": {"b": 2}} done') == '{"a": {"b": 2}}'


def test_render_annotated_places_markers():
    text = "CDL is listed on SGX. Market cap is S$6B."
    r = render_annotated(text, [cite("https://a", 21), cite("https://b", len(text)),
                                cite("https://a", 21), cite("https://c", None, "x" * 5000)])
    assert r.text == ("CDL is listed on SGX. [c1] Market cap is S$6B. [c2]\n\n"
                      "Sources consulted: [c3]")
    assert [c.url for c in r.citations] == ["https://a", "https://b", "https://c"]
    assert len(r.citations[2].cited_text) == 1500


def test_render_annotated_ignores_zero_positions():
    r = render_annotated("Listed on SGX.", [cite("https://a", 0), cite("https://b", 0)])
    assert r.text == "Listed on SGX.\n\nSources consulted: [c1][c2]"


def test_research_uses_server_web_search():
    llm, completions, log = llm_with(completion("Listed on SGX.", [cite("https://sgx", 14)]))
    r = llm.research(stage="profile", prompt_name="v1/p", prompt="find CDL", effort="xhigh",
                     max_searches=6)
    assert r.text == "Listed on SGX. [c1]" and r.citations[0].url == "https://sgx"
    call = completions.calls[0]
    assert call["model"] == "nvidia/nemotron-3.5-super-vl"
    assert call["messages"][0] == {"role": "system", "content": "system"}
    body = call["extra_body"]
    assert body["tools"] == [{"type": "openrouter:web_search",
                              "parameters": {"engine": "exa", "max_results": 5, "max_uses": 6}}]
    assert body["reasoning"] == {"effort": "high"}
    assert body["provider"] == {"require_parameters": True}
    assert log[0]["provider"] == "openrouter" and log[0]["stage"] == "profile"


def test_structured_parses_and_sends_schema():
    llm, completions, log = llm_with(completion(f"```json\n{REQUEST_JSON}\n```"))
    out = llm.structured(stage="request", prompt_name="x", prompt="parse this", schema=RequestDraft,
                         effort="low")
    assert out.client_name == "CDL"
    fmt = completions.calls[0]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert "JSON schema" in completions.calls[0]["messages"][1]["content"]
    assert log[0]["attempts"] == 1


def test_structured_retries_once_with_the_error():
    llm, completions, log = llm_with(completion('{"client_name": "CDL"}'), completion(REQUEST_JSON))
    out = llm.structured(stage="request", prompt_name="x", prompt="p", schema=RequestDraft,
                         effort="low")
    assert out.client_name == "CDL" and log[0]["attempts"] == 2
    retry = completions.calls[1]["messages"]
    assert retry[-1]["content"].startswith("That reply did not match the schema")


def test_structured_gives_up_after_retry():
    llm, _, _ = llm_with(completion("not json"), completion("still not json"))
    with pytest.raises(LLMError, match="did not match the schema"):
        llm.structured(stage="request", prompt_name="x", prompt="p", schema=RequestDraft,
                       effort="low")


def test_truncation_and_refusal():
    llm, _, _ = llm_with(completion("{", finish="length"))
    with pytest.raises(LLMError, match="max_tokens"):
        llm.structured(stage="extract:P1", prompt_name="x", prompt="p", schema=RequestDraft,
                       effort="high")
    llm, _, _ = llm_with(completion(None, refusal="no"))
    with pytest.raises(LLMRefusal):
        llm.research(stage="profile", prompt_name="x", prompt="p", effort="low")


def test_make_llm_picks_the_configured_backend(setup, monkeypatch):
    monkeypatch.delenv("ERM_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ERM_LLM_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert setup.provider == "openrouter" and setup.model == "nvidia/nemotron-3.5-super-vl"
    with pytest.raises(LLMError, match="OPENROUTER_API_KEY"):
        make_llm(setup)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    assert isinstance(make_llm(setup), OpenRouterLLM)
    monkeypatch.setenv("ERM_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    llm = make_llm(setup)
    assert isinstance(llm, ClaudeLLM) and llm.model == "claude-opus-5-5"
