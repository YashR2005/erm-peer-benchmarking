from types import SimpleNamespace as NS

import pytest

from erm_bench.llm import ClaudeLLM
from erm_bench.pipeline import Pipeline
from erm_bench.schemas import Request
from erm_bench.spend import Budget, BudgetExceeded, usage_numbers
from erm_bench.standard_setup import StandardSetup

from .fakes import FakeLLM, fake_fetch


@pytest.fixture
def claude_setup(monkeypatch) -> StandardSetup:
    monkeypatch.setenv("ERM_LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ERM_LLM_MODEL", raising=False)
    return StandardSetup.load()


def call(model, input_tokens, output_tokens, searches=0):
    return {"model": model, "usage": [{"input_tokens": input_tokens, "output_tokens": output_tokens,
                                       "server_tool_use": {"web_search_requests": searches}}]}


def test_usage_numbers_for_both_api_shapes():
    assert usage_numbers({"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 3,
                          "server_tool_use": {"web_search_requests": 2}}) == (15, 3, 2)
    assert usage_numbers({"prompt_tokens": 7, "completion_tokens": 4}) == (7, 4, 0)


def test_budget_totals_and_dollar_cap(claude_setup):
    budget = Budget(claude_setup, [call("claude-sonnet-5-5", 1_000_000, 100_000, 3)])
    assert budget.usd == pytest.approx(2.00 + 1.00 + 0.03)   # Sonnet 5.5 prices, $0.01 a search
    assert budget.summary()["searches"] == 3
    with pytest.raises(BudgetExceeded, match="Spending cap reached"):
        budget.check()                                       # cap is $3.00 per run


def test_reported_cost_is_used_when_the_api_gives_one(claude_setup):
    budget = Budget(claude_setup, [{"model": "nvidia/nemotron-3.5-super-vl",
                                    "usage": [{"prompt_tokens": 70_000, "completion_tokens": 16_000,
                                               "cost": 0.0718}]}])
    assert budget.usd == pytest.approx(0.0718)


def test_unknown_model_is_priced_as_the_most_expensive(claude_setup):
    assert Budget(claude_setup, [call("some-fallback-model", 1_000_000, 0)]).usd == pytest.approx(4.00)


def test_search_cap_shrinks_then_stops(claude_setup):
    budget = Budget(claude_setup, [call("claude-haiku-5-5", 1000, 100, 28)])
    assert budget.allow_searches(6) == 2                      # 30 per run
    budget.record("claude-haiku-5-5", [{"input_tokens": 0, "output_tokens": 0,
                                        "server_tool_use": {"web_search_requests": 2}}])
    with pytest.raises(BudgetExceeded, match="Web search cap reached"):
        budget.allow_searches(1)


class Stream:
    def __init__(self, final):
        self.final = final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(())   # no stream events needed

    def get_final_message(self):
        return self.final


class Messages:
    """Each call makes one web search."""

    def __init__(self):
        self.calls = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        usage = NS(to_dict=lambda: {"input_tokens": 100, "output_tokens": 10,
                                    "server_tool_use": {"web_search_requests": 1}})
        return Stream(NS(content=[], stop_reason="end_turn", stop_details=None,
                         model="claude-sonnet-5-5", usage=usage, parsed_output=None))


def test_claude_research_respects_the_run_budget(claude_setup):
    messages = Messages()
    client = NS(beta=NS(messages=messages))
    budget = Budget(claude_setup, [call("claude-sonnet-5-5", 0, 0, 29)])
    llm = ClaudeLLM("claude-sonnet-5-5", "system", client=client, budget=budget)
    llm.research(stage="peers", prompt_name="x", prompt="p", effort="low", max_searches=6)
    assert messages.calls[0]["tools"][0]["max_uses"] == 1     # only one search left
    with pytest.raises(BudgetExceeded):
        llm.research(stage="peers", prompt_name="x", prompt="p", effort="low", max_searches=6)
    assert len(messages.calls) == 1                           # no call once the cap is reached


def test_cap_reached_during_the_light_scan_stops_the_run(tmp_path, setup):
    llm = FakeLLM()
    original = llm.research

    def research(**kwargs):
        if kwargs["stage"].startswith("disclosure"):
            raise BudgetExceeded("Web search cap reached for this run")
        return original(**kwargs)

    llm.research = research
    p = Pipeline.start(Request(client_name="City Developments", peer_count=2), setup,
                       llm_factory=lambda s, store: llm, runs_dir=tmp_path, fetch=fake_fetch)
    with pytest.raises(BudgetExceeded):
        p.research()
    assert p.store.status == "researching"                    # can be resumed after raising the cap
