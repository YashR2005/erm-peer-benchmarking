"""Spending caps and cost estimates per run.

Costs are estimates: token counts reported by the API times the prices in
setup/<version>/method.yaml, plus a fixed price per web search. A run that
reaches its cap stops with BudgetExceeded before the next model call. One call
in flight can take a run slightly over, which the per-call search and fetch
budgets keep small.
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


class Budget:
    """Running totals for one run, checked before every model call."""

    def __init__(self, setup: StandardSetup, records: Iterable[dict] = ()):
        cfg = setup.method.get("spending", {})
        self.max_usd: float | None = cfg.get("max_usd_per_run")
        self.max_searches: int | None = cfg.get("max_web_searches_per_run")
        self.prices: dict = cfg.get("prices_per_million_tokens", {})
        self.search_usd: float = cfg.get("web_search_usd", {}).get(setup.provider, 0.01)
        self.input_tokens = self.output_tokens = self.searches = 0
        self.usd = 0.0
        self._lock = threading.Lock()
        for record in records:
            self.record(record.get("model", ""), record.get("usage") or [])

    def _price(self, model: str) -> dict:
        if model in self.prices:
            return self.prices[model]
        # Unknown model (e.g. a fallback): assume the most expensive listed, to stay safe.
        return max(self.prices.values(), key=lambda p: p["input"], default={"input": 0, "output": 0})

    def record(self, model: str, usages: list[dict]) -> None:
        price = self._price(model)
        with self._lock:
            for usage in usages:
                i, o, s = usage_numbers(usage)
                self.input_tokens += i
                self.output_tokens += o
                self.searches += s
                if usage.get("cost") is not None:  # OpenRouter reports the actual charge
                    self.usd += usage["cost"]
                else:
                    self.usd += i * price["input"] / 1e6 + o * price["output"] / 1e6 + s * self.search_usd

    def _status(self) -> str:
        parts = [f"≈ ${self.usd:.2f}" + (f" of ${self.max_usd:.2f}" if self.max_usd else "")]
        if self.max_searches:
            parts.append(f"{self.searches} of {self.max_searches} web searches")
        return ", ".join(parts)

    def check(self) -> None:
        """Raise if the run has reached its dollar cap."""
        if self.max_usd is not None and self.usd >= self.max_usd:
            raise BudgetExceeded(
                f"Spending cap reached for this run ({self._status()}). Raise the limits under "
                "'spending' in setup/v1/method.yaml to continue.")

    def allow_searches(self, wanted: int) -> int:
        """How many web searches the next call may make; raises if none are left."""
        self.check()
        if self.max_searches is None:
            return wanted
        left = self.max_searches - self.searches
        if left <= 0:
            raise BudgetExceeded(
                f"Web search cap reached for this run ({self._status()}). Raise the limits "
                "under 'spending' in setup/v1/method.yaml to continue.")
        return min(wanted, left)

    def summary(self) -> dict:
        return {"usd": round(self.usd, 4), "max_usd": self.max_usd, "searches": self.searches,
                "max_searches": self.max_searches, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens}
