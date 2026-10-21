"""The trial run's hard limits, all offline: a fake Claude client, no API key, no spend."""

import copy
import threading
import time
from types import SimpleNamespace as NS

import anthropic
import httpx2
import pytest

from erm_bench import llm as llm_module
from erm_bench.cli import main
from erm_bench.dryrun import DryRunClient
from erm_bench.llm import (
    MAX_TOKENS,
    CallCounter,
    CallLimitExceeded,
    ClaudeLLM,
    LLMError,
    PromptTooLarge,
    SpendLimitReached,
)
from erm_bench.pipeline import Pipeline, ScopeError, make_llm
from erm_bench.schemas import Request, RequestDraft
from erm_bench.spend import Budget, BudgetExceeded, token_cost

from .fakes import FakeLLM, fake_fetch

DRAFT = RequestDraft(client_name="CDL", reporting_year=None, sector=None, focus_regions=[],
                     notes=None, peer_count=None, market_cap_bucket=None, include=[], exclude=[],
                     missing=[])


class Stream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(())

    def get_final_message(self):
        return self.message


def reply(input_tokens=1000, output_tokens=500):
    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    return NS(content=[], stop_reason="end_turn", stop_details=None, parsed_output=DRAFT,
              model="claude-haiku-5-5", usage=NS(to_dict=lambda: usage))


class Client:
    """Fake Anthropic client: each stream() takes the next item, raising it if it's an error."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = 0
        self.beta = NS(messages=NS(stream=self.stream))

    def stream(self, **kwargs):
        self.requests += 1
        item = self.replies.pop(0) if self.replies else reply()
        if isinstance(item, Exception):
            raise item
        return Stream(item)


def api_error(cls, status, body, headers=None):
    response = httpx2.Response(status, headers=headers or {},
                               request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    return cls(body["error"]["message"], response=response, body=body)


def haiku(trial_setup, client, spent_usd=0.0, **kwargs):
    """A Haiku 5.5 ClaudeLLM with the trial budget, `spent_usd` already spent on output tokens."""
    records = []
    if spent_usd:
        records = [{"model": "claude-haiku-5-5",
                    "usage": [{"input_tokens": 0, "output_tokens": round(spent_usd / 0.50 * 1e6)}]}]
    return ClaudeLLM("claude-haiku-5-5", "system", client=client, server_fallback=False,
                     budget=Budget(trial_setup, records), **kwargs)


def ask(llm, prompt="p"):
    return llm.structured(stage="request", prompt_name="x", prompt=prompt, schema=RequestDraft,
                          effort="low")


# Pricing ------------------------------------------------------------------

def test_haiku_pricing_tiers_by_each_requests_whole_prompt(trial_setup):
    price = Budget(trial_setup).price("claude-haiku-5-5")
    assert token_cost(price, {"input_tokens": 100_000, "output_tokens": 1_000_000}) == \
        pytest.approx(0.01 + 0.50)
    # Over 100k (cache reads count towards it): every token at the long-context rate.
    assert token_cost(price, {"input_tokens": 60_000, "cache_read_input_tokens": 50_000,
                              "output_tokens": 1_000_000}) == \
        pytest.approx(60_000 * 0.50 / 1e6 + 50_000 * 0.05 / 1e6 + 2.50)
    assert token_cost(price, {"input_tokens": 0, "cache_read_input_tokens": 1_000_000}) == \
        pytest.approx(0.05)


def test_web_searches_cost_a_cent_each(trial_setup):
    budget = Budget(trial_setup)
    budget.record("claude-haiku-5-5", [{"input_tokens": 0, "output_tokens": 0,
                                        "server_tool_use": {"web_search_requests": 7}}])
    assert budget.usd == pytest.approx(0.07)


# The $2 cap -----------------------------------------------------------------

def test_a_call_that_could_pass_2_dollars_is_not_sent(trial_setup):
    client = Client()
    llm = haiku(trial_setup, client, spent_usd=1.997)
    with pytest.raises(BudgetExceeded, match=r"past its \$2.00 cap"):
        ask(llm)   # worst case: the prompt plus 32,000 output tokens, ≈ $0.016 → $2.013
    assert client.requests == 0


def test_after_each_call_actual_usage_is_counted_and_the_next_call_stopped(trial_setup):
    client = Client(reply(input_tokens=1000, output_tokens=8000))
    llm = haiku(trial_setup, client, spent_usd=1.98)
    ask(llm)                                       # worst case ≈ $1.996: fits under $2
    assert llm.budget.usd == pytest.approx(1.98 + 0.0001 + 0.004)
    with pytest.raises(BudgetExceeded):
        ask(llm)                                   # worst case ≈ $2.000 + : would pass $2
    assert client.requests == 1


class HonestClient(DryRunClient):
    """Reports, for every request, exactly the worst case ClaudeLLM estimated before sending it
    (as a real structured call can't exceed: its prompt is counted and its output capped)."""

    def __init__(self, setup):
        super().__init__(FakeLLM(), setup)
        self.max_risk_pages = 0        # no extra pages beyond the prompt that was counted
        self.requests = 0

    def _stream(self, **kwargs):
        self.requests += 1
        return super()._stream(**kwargs)


def test_a_full_run_stops_cleanly_at_2_dollars(tmp_path, trial_setup):
    # Prices 10x Haiku's so the trial's ~15 calls reach $2; the cap logic doesn't depend on them.
    setup = copy.deepcopy(trial_setup)
    prices = setup.method["spending"]["prices_per_million_tokens"]["claude-haiku-5-5"]
    for p in (prices, prices["long_context"]):
        for k in ("input", "output", "cache_read", "cache_write"):
            p[k] *= 10
    client = HonestClient(setup)
    made = []

    def factory(s, store):
        made.append(make_llm(setup, store, client=client))
        return made[-1]

    p = Pipeline.start(Request(client_name="City Developments", peer_count=2), setup,
                       llm_factory=factory, runs_dir=tmp_path, fetch=fake_fetch)
    with pytest.raises(BudgetExceeded, match="Stopped before the next model call"):
        p.research()
        p.lock_scope()
        p.analyse()
    budget = made[-1].budget
    assert 1.0 < budget.usd <= 2.00                # used much of the budget, never more
    assert client.requests == budget.calls         # every request sent was counted
    assert p.store.status in ("researching", "analysing")   # can resume after raising the cap


# Call caps ------------------------------------------------------------------

def test_hard_cap_of_60_calls(trial_setup):
    client = Client()
    llm = haiku(trial_setup, client, calls=CallCounter(already=59))
    ask(llm)
    with pytest.raises(CallLimitExceeded, match="hard cap of 60"):
        ask(llm)
    assert client.requests == 1


def test_call_cap_counts_earlier_attempts_of_the_run(tmp_path, trial_setup):
    p = Pipeline.start(Request(client_name="CDL", peer_count=2), trial_setup,
                       llm_factory=lambda s, store: FakeLLM(), runs_dir=tmp_path)
    for _ in range(3):
        p.store.log_llm_call({"model": "claude-haiku-5-5", "usage": [{"input_tokens": 1}] * 20})
    llm = make_llm(trial_setup, p.store, client=Client())
    assert llm.calls.count == 60 and llm.budget.calls == 60
    with pytest.raises(CallLimitExceeded):
        ask(llm)


def test_budget_caps_a_run_at_80_calls(trial_setup):
    budget = Budget(trial_setup, [{"model": "claude-haiku-5-5",
                                   "usage": [{"input_tokens": 1, "output_tokens": 1}] * 80}])
    client = Client()
    llm = ClaudeLLM("claude-haiku-5-5", "system", client=client, server_fallback=False,
                    budget=budget, calls=CallCounter(limit=1000))
    with pytest.raises(BudgetExceeded, match="80 of 80 model calls"):
        ask(llm)
    assert client.requests == 0


# Prompt size and max_tokens -------------------------------------------------

def test_a_prompt_over_100k_tokens_is_not_sent(trial_setup):
    client = Client()
    llm = haiku(trial_setup, client)
    with pytest.raises(PromptTooLarge, match="over the 100,000-token limit"):
        ask(llm, prompt="x" * 400_000)            # ~133k tokens by the local estimate
    assert client.requests == 0
    assert llm.failed == ["request (skipped: prompt over 100k tokens)"]


def test_the_token_counting_endpoint_is_used_when_available(trial_setup):
    client = Client()
    client.beta.messages.count_tokens = lambda **kw: NS(input_tokens=150_000)
    with pytest.raises(PromptTooLarge, match="150,000"):
        ask(haiku(trial_setup, client), prompt="short")
    assert client.requests == 0


def test_32000_output_tokens_on_every_call(trial_setup):
    seen = []
    client = Client()
    client.beta.messages.stream = lambda **kw: seen.append(kw) or Stream(reply())
    llm = haiku(trial_setup, client)
    ask(llm)
    llm.structured(stage="extract:client", prompt_name="x", prompt="p", schema=RequestDraft,
                   effort="high")
    assert MAX_TOKENS == 32_000 and [c["max_tokens"] for c in seen] == [32_000, 32_000]
    assert seen[0]["model"] == "claude-haiku-5-5"


def test_a_failed_call_is_still_logged_for_the_budget(trial_setup):
    log = []
    truncated = reply(output_tokens=32_000)
    truncated.stop_reason = "max_tokens"
    llm = ClaudeLLM("claude-haiku-5-5", "system", client=Client(truncated), log=log.append,
                    server_fallback=False, budget=Budget(trial_setup))
    with pytest.raises(LLMError, match="max_tokens"):
        ask(llm)
    assert Budget(trial_setup, log).usd == pytest.approx(llm.budget.usd) and log[0]["output"]["error"]


# Errors: 429 and spend limits ---------------------------------------------------

RATE_LIMITED = {"type": "error", "error": {"type": "rate_limit_error", "message": "Rate limited"}}


def test_429_waits_for_retry_after_then_retries(trial_setup, monkeypatch):
    waits = []
    monkeypatch.setattr(ClaudeLLM, "_wait", lambda self, s: waits.append(s))
    client = Client(api_error(anthropic.RateLimitError, 429, RATE_LIMITED, {"retry-after": "7"}),
                    reply())
    assert ask(haiku(trial_setup, client)) is DRAFT
    assert client.requests == 2 and waits == [7.0]


def test_429_gives_up_after_3_attempts(trial_setup, monkeypatch):
    monkeypatch.setattr(ClaudeLLM, "_wait", lambda self, s: None)
    client = Client(*[api_error(anthropic.RateLimitError, 429, RATE_LIMITED,
                                {"retry-after": "1"}) for _ in range(5)])
    with pytest.raises(anthropic.RateLimitError):
        ask(haiku(trial_setup, client))
    assert client.requests == 3


def test_spend_limit_429_stops_at_once_and_for_the_rest_of_the_run(trial_setup, monkeypatch):
    monkeypatch.setattr(ClaudeLLM, "_wait", lambda self, s: pytest.fail("must not wait"))
    body = {"type": "error", "error": {"type": "rate_limit_error",
                                       "message": "Spend limit reached",
                                       "error_code": "enforced_spend_limit_reached"}}
    client = Client(api_error(anthropic.RateLimitError, 429, body, {"retry-after": "1"}))
    llm = haiku(trial_setup, client)
    with pytest.raises(SpendLimitReached, match="spend limit"):
        ask(llm)
    with pytest.raises(SpendLimitReached):
        ask(llm)                                    # later calls aren't sent either
    assert client.requests == 1


def test_400_usage_limit_is_not_retried(trial_setup):
    body = {"type": "error", "error": {
        "type": "invalid_request_error",
        "message": "You have reached your specified API usage limits. You will regain access "
                   "on 2026-11-01 at 00:00 UTC."}}
    client = Client(api_error(anthropic.BadRequestError, 400, body))
    with pytest.raises(SpendLimitReached):
        ask(haiku(trial_setup, client))
    assert client.requests == 1


def test_the_real_client_does_no_sdk_retries(monkeypatch, trial_setup):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert ClaudeLLM("claude-haiku-5-5", "system").client.max_retries == 0


# Parallelism ----------------------------------------------------------------

def test_at_most_3_calls_run_at_once(trial_setup):
    lock, state = threading.Lock(), {"now": 0, "max": 0}

    class Slow(Stream):
        def __enter__(self):
            with lock:
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
            time.sleep(0.05)
            with lock:
                state["now"] -= 1
            return self

    client = Client()
    client.beta.messages.stream = lambda **kw: Slow(reply())
    llms = [haiku(trial_setup, client) for _ in range(2)]   # shared across ClaudeLLM instances
    threads = [threading.Thread(target=ask, args=(llms[i % 2],)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state["max"] == llm_module.MAX_PARALLEL_CALLS == 3


# Trial scope: 1 client + 2 peers --------------------------------------------

def test_trial_scans_and_benchmarks_at_most_2_peers(tmp_path, trial_setup):
    p = Pipeline.start(Request(client_name="City Developments", peer_count=5), trial_setup,
                       llm_factory=lambda s, store: FakeLLM(), runs_dir=tmp_path, fetch=fake_fetch)
    assert p.request.peer_count == 2
    p.research()
    assert len(p.peers.candidates) == 2
    assert {d.company_id for d in p.documents} == {"client", "P1", "P2"}
    with pytest.raises(ScopeError, match="At most 2 peers"):
        p.set_peer_count(3)


# Dry run ------------------------------------------------------------------

def test_dry_run_needs_no_key_and_reports_calls_and_cost(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ERM_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ERM_LLM_MODEL", "claude-haiku-5-5")
    assert main(["run", "City Developments", "--dry-run"]) == 0
    out = capsys.readouterr()
    assert "DRY RUN: no API calls were made" in out.out and "extract:client" in out.out
    assert "[llm] profile:" in out.err and "=== Run" in out.err
