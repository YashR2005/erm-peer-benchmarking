"""Spending caps and cost estimates per run.

Costs are estimates: token counts reported by the API times the prices in
setup/<version>/method.yaml, plus a fixed price per web search. Before each
Claude call, begin_call() reserves that call's estimated worst-case cost and
refuses the call if the run could then pass its dollar cap, or its call cap;
after the call, record() adds what the API reports it used. So a run stops with
BudgetExceeded before a call that could take it over the cap, and parallel
calls can't overshoot together.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable

from .standard_setup import StandardSetup


class BudgetExceeded(RuntimeError):
    pass


def usage_numbers(usage: dict) -> tuple[int, int, int]:
    """(input tokens, output tokens, web searches) from a Claude or OpenAI-style usage dict."""
    if "prompt_tokens" in usage:  # OpenAI / OpenRouter
        input_tokens = usage.get("prompt_tokens") or 0
        output_tokens = usage.get("completion_tokens") or 0
    else:  # Claude: cache writes and reads are input too
        input_tokens = sum(usage.get(k) or 0 for k in (
            "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        output_tokens = usage.get("output_tokens") or 0
    searches = (usage.get("server_tool_use") or {}).get("web_search_requests") or 0
    return input_tokens, output_tokens, searches


def tier(price: dict, prompt_tokens: int) -> dict:
    """The prices that apply to one request. Some models (Haiku 5.5) charge more per token when
    the request's whole prompt (input plus cache reads and writes) is over a threshold."""
    long = price.get("long_context")
    if long and prompt_tokens > long["above_input_tokens"]:
        return {**price, **{k: v for k, v in long.items() if k != "above_input_tokens"}}
    return price


def token_cost(price: dict, usage: dict) -> float:
    """USD for one request's tokens (web searches not included)."""
    if "prompt_tokens" in usage:  # OpenAI / OpenRouter
        i, o, _ = usage_numbers(usage)
        return i * price["input"] / 1e6 + o * price["output"] / 1e6
    fresh = usage.get("input_tokens") or 0
    written = usage.get("cache_creation_input_tokens") or 0
    read = usage.get("cache_read_input_tokens") or 0
    p = tier(price, fresh + written + read)
    return (fresh * p["input"] + written * p.get("cache_write", p["input"])
            + read * p.get("cache_read", p["input"])
            + (usage.get("output_tokens") or 0) * p["output"]) / 1e6


class Budget:
    """Running totals for one run, checked before every model call."""

    def __init__(self, setup: StandardSetup, records: Iterable[dict] = ()):
        cfg = setup.method.get("spending", {})
        self.max_usd: float | None = cfg.get("max_usd_per_run")
        self.max_searches: int | None = cfg.get("max_web_searches_per_run")
        self.max_calls: int | None = cfg.get("max_calls_per_run")
        self.prices: dict = cfg.get("prices_per_million_tokens", {})
        self.search_usd: float = cfg.get("web_search_usd", {}).get(setup.provider, 0.01)
        self.input_tokens = self.output_tokens = self.searches = self.calls = 0
        self.reserved = 0  # searches granted to calls still running
        self.reserved_usd = 0.0  # estimated cost of calls still running
        self.in_flight = 0
        self.usd = 0.0
        self._lock = threading.Lock()
        for record in records:
            self.record(record.get("model", ""), record.get("usage") or [])

    def price(self, model: str) -> dict:
        if model in self.prices:
            return self.prices[model]
        # Unknown model (e.g. a fallback): assume the most expensive listed, to stay safe.
        return max(self.prices.values(), key=lambda p: p["input"], default={"input": 0, "output": 0})

    def record(self, model: str, usages: list[dict]) -> None:
        """Add what the API reports for finished requests (one usage dict per request)."""
        price = self.price(model)
        with self._lock:
            for usage in usages:
                i, o, s = usage_numbers(usage)
                self.input_tokens += i
                self.output_tokens += o
                self.searches += s
                self.calls += 1
                if usage.get("cost") is not None:  # OpenRouter reports the actual charge
                    self.usd += usage["cost"]
                else:
                    self.usd += token_cost(price, usage) + s * self.search_usd

    def _status(self) -> str:
        parts = [f"≈ ${self.usd:.2f}" + (f" of ${self.max_usd:.2f}" if self.max_usd else "")]
        if self.max_searches:
            parts.append(f"{self.searches} of {self.max_searches} web searches")
        if self.max_calls:
            parts.append(f"{self.calls} of {self.max_calls} model calls")
        return ", ".join(parts)

    def check(self) -> None:
        """Raise if the run has reached its dollar cap."""
        if self.max_usd is not None and self.usd >= self.max_usd:
            raise BudgetExceeded(
                f"Spending cap reached for this run ({self._status()}). Raise the limits under "
                "'spending' in setup/v1/method.yaml to continue.")

    def begin_call(self, estimate_usd: float) -> None:
        """Before a call: raise if it could take the run past its dollar or call cap, counting
        the calls still running; otherwise reserve its estimated cost. Pair with end_call()."""
        self.check()
        with self._lock:
            if self.max_calls is not None and self.calls + self.in_flight >= self.max_calls:
                raise BudgetExceeded(
                    f"Model call cap reached for this run ({self._status()}). Raise "
                    "max_calls_per_run under 'spending' in setup/v1/method.yaml to continue.")
            committed = self.usd + self.reserved_usd
            if self.max_usd is not None and committed + estimate_usd > self.max_usd:
                raise BudgetExceeded(
                    f"Stopped before the next model call: it could cost up to ≈ ${estimate_usd:.2f}, "
                    f"which would take this run past its ${self.max_usd:.2f} cap ({self._status()}"
                    f"{f', ≈ ${self.reserved_usd:.2f} in calls still running' if self.in_flight else ''}"
                    "). Raise max_usd_per_run under 'spending' in setup/v1/method.yaml to continue.")
            self.reserved_usd += estimate_usd
            self.in_flight += 1

    def end_call(self, estimate_usd: float) -> None:
        """End a reservation; what the call actually used was counted by record()."""
        with self._lock:
            self.reserved_usd = max(0.0, self.reserved_usd - estimate_usd)
            self.in_flight = max(0, self.in_flight - 1)

    def allow_searches(self, wanted: int) -> int:
        """Reserve up to `wanted` web searches for the next call; raises if none are left.
        Calls running in parallel each get a share, so together they can't pass the cap.
        Pair with release() when the call ends."""
        self.check()
        if self.max_searches is None:
            return wanted
        with self._lock:
            left = self.max_searches - self.searches - self.reserved
            if left <= 0:
                raise BudgetExceeded(
                    f"Web search cap reached for this run ({self._status()}). Raise the limits "
                    "under 'spending' in setup/v1/method.yaml to continue.")
            granted = min(wanted, left)
            self.reserved += granted
            return granted

    def release(self, granted: int) -> None:
        """End a reservation; the searches actually made were counted by record()."""
        if self.max_searches is not None:
            with self._lock:
                self.reserved = max(0, self.reserved - granted)

    def summary(self) -> dict:
        return {"usd": round(self.usd, 4), "max_usd": self.max_usd, "searches": self.searches,
                "max_searches": self.max_searches, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "calls": self.calls,
                "max_calls": self.max_calls}
