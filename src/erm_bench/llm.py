"""Claude calls, behind a small interface so stages can be tested with a fake.

Two kinds of call:

- research(): web search and web fetch, free-text notes. Citations come from
  the API (url + cited text), so every source is one the model actually saw.
- structured(): turns notes or document text into a Pydantic model through
  structured outputs. Structured outputs can't be combined with citations,
  which is why web-backed stages make one call of each kind.

Every call is logged to the run's llm_calls.jsonl.

Hard limits around every request (ClaudeLLM._call): prompts over 100k tokens are
not sent, a run makes at most 60 requests, at most 3 run at once, and the run's
Budget is checked before (estimate) and after (reported usage) each one. A 429
is retried after its retry-after delay (3 attempts); a spend-limit error stops
the run at once.
"""

from __future__ import annotations

import hashlib
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

import anthropic
from pydantic import BaseModel

from .schemas import Source
from .spend import Budget, token_cost, usage_numbers
from .timing import Clock, TimeLimitExceeded

T = TypeVar("T", bound=BaseModel)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 32_000           # output per call; at Haiku 5.5 prices at most ≈ $0.016 a call
MAX_CONTINUATIONS = 5
MAX_PROMPT_TOKENS = 100_000   # Haiku 5.5 charges 5x per token for prompts above this
MAX_CALLS_PER_RUN = 60        # hard cap, independent of the Budget's own call cap
MAX_PARALLEL_CALLS = 3
RETRY_ATTEMPTS = 3            # for HTTP 429 (and 5xx); spend-limit errors are never retried
MAX_RETRY_WAIT = 60.0         # seconds
SEARCH_RESULT_TOKENS = 3000   # assumed size of one web search's results, for estimates

# Shared by every ClaudeLLM in the process: the API server can run several jobs at once.
_parallel = threading.BoundedSemaphore(MAX_PARALLEL_CALLS)


class LLMError(RuntimeError):
    pass


class LLMRefusal(LLMError):
    pass


class PromptTooLarge(LLMError):
    """The prompt is over MAX_PROMPT_TOKENS, so it was not sent."""


class SpendLimitReached(LLMError):
    """The API refused a call because the account reached its spend limit."""


class CallLimitExceeded(LLMError):
    pass


class CallCounter:
    """The hard cap on model requests per run: a plain counter, separate from the Budget."""

    def __init__(self, limit: int = MAX_CALLS_PER_RUN, already: int = 0):
        self.limit = limit
        self.count = already
        self._lock = threading.Lock()

    def take(self) -> None:
        with self._lock:
            if self.count >= self.limit:
                raise CallLimitExceeded(
                    f"Stopped: this run has made {self.count} model calls, the hard cap of "
                    f"{self.limit} (MAX_CALLS_PER_RUN in src/erm_bench/llm.py).")
            self.count += 1


def estimate_tokens(text: str) -> int:
    """Local token estimate when the counting endpoint isn't available. English runs at about
    4 characters a token; 3 errs on the high side."""
    return len(text) // 3 + 1


def research_input_estimate(prompt_tokens: int, searches: int, fetches: int,
                            fetch_tokens: int) -> int:
    """Worst-case billed input for one research request. Each server-side search or fetch step
    re-reads the prompt and the results so far, so input grows with the square of the steps."""
    steps = searches + fetches
    results = searches * SEARCH_RESULT_TOKENS + fetches * fetch_tokens
    return (steps + 1) * prompt_tokens + results * (steps + 1) // 2


def is_spend_limit_error(e: anthropic.APIStatusError) -> bool:
    text = f"{e.message} {e.body}".lower()
    if e.status_code == 429:
        return "enforced_spend_limit_reached" in text
    if e.status_code == 400:
        return re.search(r"reached your specified\b.*\busage limit", text) is not None
    return False


def retry_wait(e: anthropic.APIStatusError, attempt: int) -> float:
    try:
        return min(float(e.response.headers.get("retry-after")), MAX_RETRY_WAIT)
    except (AttributeError, TypeError, ValueError):  # no usable header: back off
        return min(2.0 ** attempt, MAX_RETRY_WAIT)


@dataclass
class ResearchResult:
    """Research notes with [cN] markers, and the sources those markers point to."""

    text: str
    citations: list[Source] = field(default_factory=list)

    def citations_block(self) -> str:
        if not self.citations:
            return "(no citations)"
        lines = []
        for i, src in enumerate(self.citations, start=1):
            quote = f' — "{src.cited_text.strip()}"' if src.cited_text else " — (page fetched)"
            lines.append(f"[c{i}] {src.url}{quote}")
        return "\n".join(lines)

    def resolve(self, citation_ids: list[str]) -> list[Source]:
        sources = []
        for cid in citation_ids:
            digits = cid.strip().lstrip("[").rstrip("]").lower().removeprefix("c")
            if digits.isdigit() and 1 <= int(digits) <= len(self.citations):
                src = self.citations[int(digits) - 1]
                if src not in sources:
                    sources.append(src)
        return sources


class LLM(Protocol):
    def research(self, *, stage: str, prompt_name: str, prompt: str, effort: str,
                 max_searches: int = 5, max_fetches: int = 2) -> ResearchResult: ...

    def structured(self, *, stage: str, prompt_name: str, prompt: str, schema: type[T],
                   effort: str) -> T: ...


def render_research(blocks: list) -> ResearchResult:
    """Flatten response content into notes with citation markers.

    Text blocks carry web-search citations (url, cited_text). Pages the model
    fetched are added as sources too, without cited text. If the answer has no
    citations at all (the dynamic-filtering tools return none), the search results
    the model saw are listed as sources instead, so the structuring step can still
    attribute facts.
    """
    citations: list[Source] = []
    index: dict[tuple[str, str], int] = {}
    searched: list[tuple[str, str]] = []

    def add(url: str, title: str, cited: str) -> int:
        key = (url, cited)
        if key not in index:
            citations.append(Source(url=url, title=title, cited_text=cited))
            index[key] = len(citations)
        return index[key]

    parts: list[str] = []
    for block in blocks:
        kind = getattr(block, "type", None)
        if kind == "text":
            markers = []
            for cit in getattr(block, "citations", None) or []:
                url = getattr(cit, "url", None)
                if not url:
                    continue
                n = add(url, getattr(cit, "title", "") or "", getattr(cit, "cited_text", "") or "")
                markers.append(f"[c{n}]")
            suffix = (" " + "".join(dict.fromkeys(markers))) if markers else ""
            parts.append(block.text + suffix)
        elif kind == "web_fetch_tool_result":
            content = getattr(block, "content", None)
            url = getattr(content, "url", None)
            if url:
                n = add(url, "fetched page", "")
                parts.append(f"\n(fetched {url} [c{n}])\n")
        elif kind == "web_search_tool_result":
            results = getattr(block, "content", None)
            if isinstance(results, list):  # an error result is a single object, not a list
                searched += [(r.url, getattr(r, "title", "") or "") for r in results
                             if getattr(r, "url", None)]
    text = "".join(parts).strip()
    if not any(c.cited_text for c in citations) and searched:
        markers = [f"[c{add(url, title, '')}]" for url, title in dict.fromkeys(searched)]
        text += "\n\nSearch results consulted: " + "".join(markers)
    return ResearchResult(text=text, citations=citations)


class ClaudeLLM:
    def __init__(self, model: str, system: str, log: Callable[[dict], None] | None = None,
                 client: anthropic.Anthropic | None = None,
                 web_search_tool: str = "web_search_20250305",
                 web_fetch_tool: str = "web_fetch_20250910", max_fetch_tokens: int = 6000,
                 budget: Budget | None = None, clock: Clock | None = None,
                 server_fallback: bool = True, calls: CallCounter | None = None):
        self.model = model
        self.server_fallback = server_fallback
        self.budget = budget
        self.clock = clock
        self.calls = calls or CallCounter()
        self.system = system
        self.log = log or (lambda record: None)
        # No SDK retries: _send retries 429s itself, so a spend-limit 429 is never retried.
        self.client = client or anthropic.Anthropic(max_retries=0)
        self.web_search_tool = web_search_tool
        self.web_fetch_tool = web_fetch_tool
        self.max_fetch_tokens = max_fetch_tokens
        self.halted: str | None = None   # set when the API reports the account's spend limit
        self.steps: dict[str, dict] = {}  # per stage: requests, tokens, cost (this process)
        self.failed: list[str] = []
        self._lock = threading.Lock()

    def research(self, *, stage: str, prompt_name: str, prompt: str, effort: str,
                 max_searches: int = 5, max_fetches: int = 2) -> ResearchResult:
        try:
            return self._research(stage, prompt_name, prompt, effort, max_searches, max_fetches)
        except Exception as e:
            self._failed(stage, e)
            raise

    def structured(self, *, stage: str, prompt_name: str, prompt: str, schema: type[T],
                   effort: str) -> T:
        try:
            return self._structured(stage, prompt_name, prompt, schema, effort)
        except Exception as e:
            self._failed(stage, e)
            raise

    def summary_lines(self) -> list[str]:
        """Totals for the whole run (earlier attempts included) and the steps that failed."""
        lines = [f"Model calls: {self.calls.count} (hard cap {self.calls.limit})"]
        if self.budget:
            b = self.budget
            lines.append(f"Tokens: {b.input_tokens:,} input, {b.output_tokens:,} output; "
                         f"web searches: {b.searches}")
            lines.append(f"Cost: ≈ ${b.usd:.4f}" + (f" of ${b.max_usd:.2f}" if b.max_usd else ""))
        lines.append("Failed or skipped steps: " + (", ".join(self.failed) or "none"))
        return lines

    # Internals

    def _research(self, stage, prompt_name, prompt, effort, max_searches, max_fetches):
        user = {"role": "user", "content": prompt}
        blocks: list = []
        started = time.monotonic()
        usage: list[dict] = []
        for _ in range(MAX_CONTINUATIONS + 1):
            searches = self.budget.allow_searches(max_searches) if self.budget else max_searches
            tools = [{"type": self.web_search_tool, "name": "web_search", "max_uses": searches}]
            if max_fetches:
                # Cap each fetched page: research finds sources, it doesn't read whole PDFs.
                tools.append({"type": self.web_fetch_tool, "name": "web_fetch",
                              "max_uses": max_fetches, "max_content_tokens": self.max_fetch_tokens})
            messages = [user] if not blocks else [user, {"role": "assistant", "content": blocks}]
            try:
                final, used = self._call(stage, messages, searches=searches, fetches=max_fetches,
                                         tools=tools, output_config={"effort": effort})
                usage.append(used)
            except LLMError as e:  # log what earlier requests used: the budget is rebuilt from it
                if usage:
                    self._log(stage, prompt_name, prompt, effort, final, usage, started,
                              output={"error": str(e)})
                raise
            finally:
                if self.budget:
                    self.budget.release(searches)
            try:
                self._check_stop(final, stage)
            except LLMError as e:
                self._log(stage, prompt_name, prompt, effort, final, usage, started,
                          output={"error": str(e)})
                raise
            blocks = blocks + list(final.content)
            if final.stop_reason != "pause_turn":
                break
        else:
            raise LLMError(f"{stage}: research still paused after {MAX_CONTINUATIONS} continuations")
        result = render_research(blocks)
        self._log(stage, prompt_name, prompt, effort, final, usage, started,
                  output={"text": result.text, "citations": [c.model_dump() for c in result.citations]})
        return result

    def _structured(self, stage, prompt_name, prompt, schema, effort):
        started = time.monotonic()
        final, used = self._call(stage, [{"role": "user", "content": prompt}],
                                 output_format=schema, output_config={"effort": effort})
        try:
            self._check_stop(final, stage)
            if final.stop_reason == "max_tokens":
                raise LLMError(f"{stage}: output hit max_tokens before the JSON was complete")
            parsed = final.parsed_output
            if parsed is None:
                raise LLMError(f"{stage}: no structured output returned")
        except LLMError as e:  # still log it: the run's budget is rebuilt from this log
            self._log(stage, prompt_name, prompt, effort, final, [used], started,
                      output={"error": str(e)})
            raise
        self._log(stage, prompt_name, prompt, effort, final, [used], started,
                  output=parsed.model_dump(mode="json"))
        return parsed

    def _call(self, stage: str, messages: list, *, searches: int = 0, fetches: int = 0,
              **params):
        """One API request inside the run's hard limits. Returns (message, usage dict)."""
        if self.halted:
            raise SpendLimitReached(self.halted)
        prompt_tokens = self._count_tokens(messages)
        if prompt_tokens > MAX_PROMPT_TOKENS:
            raise PromptTooLarge(
                f"{stage}: the prompt is about {prompt_tokens:,} tokens, over the "
                f"{MAX_PROMPT_TOKENS:,}-token limit per call (Haiku 5.5 charges 5x per token above "
                "it), so it was not sent.")
        estimate = 0.0
        if self.budget:
            input_tokens = (research_input_estimate(prompt_tokens, searches, fetches,
                                                    self.max_fetch_tokens)
                            if searches or fetches else prompt_tokens)
            estimate = (token_cost(self.budget.price(self.model),
                                   {"input_tokens": input_tokens, "output_tokens": MAX_TOKENS})
                        + searches * self.budget.search_usd)
        self.calls.take()
        if self.budget:
            self.budget.begin_call(estimate)
        try:
            with _parallel:
                final = self._send(stage, messages=messages, **params)
        finally:
            if self.budget:
                self.budget.end_call(estimate)
        used = final.usage.to_dict()
        if self.budget:
            self.budget.record(final.model, [used])
        self._report(stage, prompt_tokens, used, final.model)
        return final, used

    def _count_tokens(self, messages: list) -> int:
        try:
            return self.client.beta.messages.count_tokens(
                model=self.model, system=self.system, messages=messages).input_tokens
        except Exception:  # endpoint unavailable (e.g. a test client) or failed: estimate
            return estimate_tokens(self.system + repr(messages))

    def _send(self, stage: str, **params):
        """_stream, retrying HTTP 429 (after its retry-after delay) and 5xx up to RETRY_ATTEMPTS
        times. A spend-limit error stops this and every later call in the run."""
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                return self._stream(stage, **params)
            except anthropic.APIStatusError as e:
                if is_spend_limit_error(e):
                    self.halted = (
                        f"Stopped: the Claude API refused the {stage} call because the account "
                        f"reached its spend limit (HTTP {e.status_code}). Nothing was retried and "
                        "this run makes no more calls. Raise the limit in the Claude Console, "
                        "then restart the server and resume the run.")
                    raise SpendLimitReached(self.halted) from e
                if not (e.status_code == 429 or e.status_code >= 500) or attempt == RETRY_ATTEMPTS:
                    raise
                wait, reason = retry_wait(e, attempt), f"HTTP {e.status_code}"
            except anthropic.APIConnectionError as e:
                if isinstance(e, anthropic.APITimeoutError) or attempt == RETRY_ATTEMPTS:
                    raise
                wait, reason = min(2.0 ** attempt, MAX_RETRY_WAIT), "connection error"
            print(f"[llm] {stage}: {reason}; retrying in {wait:.0f}s "
                  f"(attempt {attempt + 1} of {RETRY_ATTEMPTS})", file=sys.stderr, flush=True)
            self._wait(wait)

    def _wait(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            if self.clock:
                self.clock.check()  # Stop pressed, or the stage ran out of time
            time.sleep(min(left, 0.5))

    def _stream(self, stage: str, **params):
        limit, message = self.clock.call_limit(stage) if self.clock else (None, "")
        client = self.client.with_options(timeout=limit) if limit else self.client
        started = time.monotonic()
        # Server-side fallback on a refusal, for models that have one (not Haiku 5.5).
        fallback = {"betas": [FALLBACK_BETA], "fallbacks": "default"} if self.server_fallback else {}
        with client.beta.messages.stream(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=self.system,
            **fallback,
            **params,
        ) as stream:
            for _event in stream:  # leaving the block closes the connection and the call
                if limit and time.monotonic() - started > limit:
                    raise TimeLimitExceeded(message)
                if self.clock:
                    self.clock.check()  # Stop pressed
            return stream.get_final_message()

    def _report(self, stage: str, prompt_tokens: int, used: dict, model: str) -> None:
        """Print a running cost line after every request."""
        i, o, s = usage_numbers(used)
        cost = (token_cost(self.budget.price(model), used) + s * self.budget.search_usd
                if self.budget else 0.0)
        with self._lock:
            step = self.steps.setdefault(stage, {"calls": 0, "prompt_tokens": 0, "input_tokens": 0,
                                                 "output_tokens": 0, "searches": 0, "usd": 0.0})
            for key, value in (("calls", 1), ("prompt_tokens", prompt_tokens), ("input_tokens", i),
                               ("output_tokens", o), ("searches", s), ("usd", cost)):
                step[key] += value
        run = ""
        if self.budget:
            cap = f" / ${self.budget.max_usd:.2f}" if self.budget.max_usd else ""
            run = f" · run ≈ ${self.budget.usd:.4f}{cap}"
        print(f"[llm] {stage}: in {i:,} · out {o:,}" + (f" · {s} searches" if s else "")
              + f" · ≈ ${cost:.4f}{run} · call {self.calls.count}/{self.calls.limit}",
              file=sys.stderr, flush=True)

    def _failed(self, stage: str, e: Exception) -> None:
        label = "skipped: prompt over 100k tokens" if isinstance(e, PromptTooLarge) else type(e).__name__
        with self._lock:
            self.failed.append(f"{stage} ({label})")

    @staticmethod
    def _check_stop(message, stage: str) -> None:
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None)
            raise LLMRefusal(f"{stage}: the model declined (category: {category})")

    def _log(self, stage, prompt_name, prompt, effort, final, usage, started, output) -> None:
        self.log({
            "stage": stage,
            "prompt": prompt_name,
            "model": final.model,
            "effort": effort,
            "input_sha256": hashlib.sha256(prompt.encode()).hexdigest()[:16],
            "stop_reason": final.stop_reason,
            "request_id": getattr(final, "_request_id", None),
            "usage": usage,
            "seconds": round(time.monotonic() - started, 1),
            "output": output,
        })
