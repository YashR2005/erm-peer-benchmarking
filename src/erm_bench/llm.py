"""Claude calls, behind a small interface so stages can be tested with a fake.

Two kinds of call:

- research(): web search and web fetch, free-text notes. Citations come from
  the API (url + cited text), so every source is one the model actually saw.
- structured(): turns notes or document text into a Pydantic model through
  structured outputs. Structured outputs can't be combined with citations,
  which is why web-backed stages make one call of each kind.

Every call is logged to the run's llm_calls.jsonl.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

import anthropic
from pydantic import BaseModel

from .schemas import Source
from .spend import Budget

T = TypeVar("T", bound=BaseModel)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 64000
MAX_CONTINUATIONS = 5


class LLMError(RuntimeError):
    pass


class LLMRefusal(LLMError):
    pass


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
                 budget: Budget | None = None):
        self.model = model
        self.budget = budget
        self.system = system
        self.log = log or (lambda record: None)
        self.client = client or anthropic.Anthropic()
        self.web_search_tool = web_search_tool
        self.web_fetch_tool = web_fetch_tool
        self.max_fetch_tokens = max_fetch_tokens

    def research(self, *, stage: str, prompt_name: str, prompt: str, effort: str,
                 max_searches: int = 5, max_fetches: int = 2) -> ResearchResult:
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
            final = self._stream(messages=messages, tools=tools, output_config={"effort": effort})
            usage.append(final.usage.to_dict())
            if self.budget:
                self.budget.record(final.model, usage[-1:])
            self._check_stop(final, stage)
            blocks = blocks + list(final.content)
            if final.stop_reason != "pause_turn":
                break
        else:
            raise LLMError(f"{stage}: research still paused after {MAX_CONTINUATIONS} continuations")
        result = render_research(blocks)
        self._log(stage, prompt_name, prompt, effort, final, usage, started,
                  output={"text": result.text, "citations": [c.model_dump() for c in result.citations]})
        return result

    def structured(self, *, stage: str, prompt_name: str, prompt: str, schema: type[T],
                   effort: str) -> T:
        started = time.monotonic()
        if self.budget:
            self.budget.check()
        final = self._stream(messages=[{"role": "user", "content": prompt}],
                             output_format=schema, output_config={"effort": effort})
        if self.budget:
            self.budget.record(final.model, [final.usage.to_dict()])
        self._check_stop(final, stage)
        if final.stop_reason == "max_tokens":
            raise LLMError(f"{stage}: output hit max_tokens before the JSON was complete")
        parsed = final.parsed_output
        if parsed is None:
            raise LLMError(f"{stage}: no structured output returned")
        self._log(stage, prompt_name, prompt, effort, final, [final.usage.to_dict()], started,
                  output=parsed.model_dump(mode="json"))
        return parsed

    # Internals

    def _stream(self, **params):
        with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=self.system,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            **params,
        ) as stream:
            return stream.get_final_message()

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
