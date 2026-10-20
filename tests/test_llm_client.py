from types import SimpleNamespace as NS

import pytest

from erm_bench.llm import FALLBACK_BETA, ClaudeLLM, LLMError, LLMRefusal
from erm_bench.schemas import RequestDraft


def message(content, stop_reason="end_turn", parsed=None):
    return NS(content=content, stop_reason=stop_reason, stop_details=None, parsed_output=parsed,
              model="claude-opus-5-5", usage=NS(to_dict=lambda: {"output_tokens": 10}))


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(())   # no stream events needed

    def get_final_message(self):
        return self.msg


class FakeMessages:
    def __init__(self, *msgs):
        self.msgs, self.calls = list(msgs), []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return FakeStream(self.msgs.pop(0))


def llm_with(*msgs):
    messages = FakeMessages(*msgs)
    log: list[dict] = []
    llm = ClaudeLLM("claude-opus-5-5", "system", log=log.append,
                    client=NS(beta=NS(messages=messages)))
    return llm, messages, log


def text(t, url=None):
    cits = [NS(url=url, title="", cited_text="quote")] if url else None
    return NS(type="text", text=t, citations=cits)


def test_research_resumes_after_pause_turn():
    first = [text("Part one.", "https://a")]
    llm, messages, log = llm_with(message(first, "pause_turn"),
                                  message([text(" Part two.", "https://b")]))
    result = llm.research(stage="profile", prompt_name="v1/x", prompt="find", effort="medium")
    assert result.text == "Part one. [c1] Part two. [c2]"
    assert len(messages.calls) == 2
    resumed = messages.calls[1]["messages"]
    assert resumed[1] == {"role": "assistant", "content": first}   # no extra "continue" turn
    call = messages.calls[0]
    assert call["fallbacks"] == "default" and call["betas"] == [FALLBACK_BETA]
    assert call["model"] == "claude-opus-5-5" and call["output_config"] == {"effort": "medium"}
    assert [(t["type"], t["name"]) for t in call["tools"]] == [
        ("web_search_20250305", "web_search"), ("web_fetch_20250910", "web_fetch")]
    assert call["tools"][1]["max_content_tokens"] == 6000 and call["tools"][1]["max_uses"] == 2
    assert log[0]["stage"] == "profile" and len(log[0]["usage"]) == 2


def test_refusal_raises():
    llm, _, _ = llm_with(message([], "refusal"))
    with pytest.raises(LLMRefusal):
        llm.research(stage="profile", prompt_name="x", prompt="p", effort="low")


def test_structured_returns_parsed_output():
    draft = RequestDraft(client_name="CDL", reporting_year=None, sector=None, focus_regions=[],
                         notes=None, peer_count=None, market_cap_bucket=None, include=[],
                         exclude=[], missing=[])
    llm, messages, log = llm_with(message([], parsed=draft))
    out = llm.structured(stage="request", prompt_name="x", prompt="p", schema=RequestDraft,
                         effort="low")
    assert out is draft
    assert messages.calls[0]["output_format"] is RequestDraft
    assert log[0]["output"]["client_name"] == "CDL"


def test_structured_truncated_output_raises():
    llm, _, _ = llm_with(message([], "max_tokens"))
    with pytest.raises(LLMError, match="max_tokens"):
        llm.structured(stage="extract:P1", prompt_name="x", prompt="p", schema=RequestDraft,
                       effort="high")
