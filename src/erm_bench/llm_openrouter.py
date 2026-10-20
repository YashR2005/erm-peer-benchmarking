"""OpenRouter backend (OpenAI-compatible), default model NVIDIA Nemotron 3.5 Super VL.

Same two kinds of call as the Claude backend:

- research(): OpenRouter's server-side web search tool. The model writes its
  own queries; sources come back as url_citation annotations (url, title and
  the excerpt the model read), so every source is one it actually saw.
- structured(): response_format with a strict JSON schema. The schema is also
  given in the prompt, and output that doesn't validate gets one retry with
  the validation error, since providers differ in how strictly they enforce it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from typing import TypeVar

import openai
from pydantic import BaseModel, TypeAdapter, ValidationError

from .llm import LLMError, LLMRefusal, ResearchResult
from .schemas import Source
from .spend import Budget
from .timing import Clock, TimeLimitExceeded

T = TypeVar("T", bound=BaseModel)

MAX_TOKENS = 32000
EXCERPT_CHARS = 1500
EFFORTS = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}


def strict_json_schema(schema: type[BaseModel]) -> dict:
    """Pydantic JSON schema made strict: no extra properties, every property required."""
    root = TypeAdapter(schema).json_schema()

    def fix(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                fix(value)
        elif isinstance(node, list):
            for value in node:
                fix(value)

    fix(root)
    return root


def json_text(text: str) -> str:
    """The JSON object in a reply, without code fences or surrounding prose."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text


def _field(obj: object, name: str):
    if isinstance(obj, dict):
        return obj.get(name)
    value = getattr(obj, name, None)
    if value is None and getattr(obj, "model_extra", None):
        value = obj.model_extra.get(name)
    return value


def render_annotated(content: str, annotations: list) -> ResearchResult:
    """Notes with [cN] markers placed where each url_citation ends."""
    citations: list[Source] = []
    index: dict[tuple[str, str], int] = {}
    placed: dict[int, list[int]] = {}
    unplaced: list[int] = []
    for ann in annotations or []:
        cite = _field(ann, "url_citation")
        url = _field(cite, "url") if cite is not None else None
        if not url:
            continue
        excerpt = (_field(cite, "content") or "").strip()[:EXCERPT_CHARS]
        key = (url, excerpt)
        if key not in index:
            citations.append(Source(url=url, title=_field(cite, "title") or "", cited_text=excerpt))
            index[key] = len(citations)
        n = index[key]
        end = _field(cite, "end_index")
        # Some providers return 0 for every citation; that says nothing about where it applies.
        if isinstance(end, int) and 0 < end <= len(content):
            placed.setdefault(end, []).append(n)
        else:
            unplaced.append(n)
    text = content
    for pos in sorted(placed, reverse=True):
        markers = "".join(f"[c{n}]" for n in dict.fromkeys(placed[pos]))
        text = f"{text[:pos]} {markers}{text[pos:]}"
    if unplaced:
        text += "\n\nSources consulted: " + "".join(f"[c{n}]" for n in dict.fromkeys(unplaced))
    return ResearchResult(text=text.strip(), citations=citations)


class OpenRouterLLM:
    def __init__(self, model: str, system: str, log: Callable[[dict], None] | None = None,
                 client: openai.OpenAI | None = None, base_url: str = "https://openrouter.ai/api/v1",
                 web_search: dict | None = None, timeout: float = 600, budget: Budget | None = None,
                 clock: Clock | None = None):
        if client is None:
            key = os.environ.get("OPENROUTER_API_KEY")
            if not key:
                raise LLMError("OPENROUTER_API_KEY is not set. Add it to .env.")
            client = openai.OpenAI(api_key=key, base_url=base_url, timeout=timeout,
                                   default_headers={"X-Title": "ERM peer benchmarking"})
        self.client = client
        self.model = model
        self.system = system
        self.log = log or (lambda record: None)
        self.web_search = web_search or {"engine": "exa", "max_results": 5}
        self.budget = budget
        self.clock = clock

    def research(self, *, stage: str, prompt_name: str, prompt: str, effort: str,
                 max_searches: int = 5, max_fetches: int = 2) -> ResearchResult:
        if self.budget:
            max_searches = self.budget.allow_searches(max_searches)
        tool = {"type": "openrouter:web_search",
                "parameters": {**self.web_search, "max_uses": max_searches}}
        started = time.monotonic()
        try:
            response = self._create(stage, [{"role": "user", "content": prompt}], effort,
                                    extra={"tools": [tool]})
        finally:
            if self.budget:
                self.budget.release(max_searches)
        message = response.choices[0].message
        result = render_annotated(message.content or "", message.annotations or [])
        self._log(stage, prompt_name, prompt, effort, response, started,
                  output={"text": result.text, "citations": [c.model_dump() for c in result.citations]})
        return result

    def structured(self, *, stage: str, prompt_name: str, prompt: str, schema: type[T],
                   effort: str) -> T:
        strict = strict_json_schema(schema)
        response_format = {"type": "json_schema",
                           "json_schema": {"name": schema.__name__, "strict": True, "schema": strict}}
        messages = [{"role": "user", "content": (
            f"{prompt}\n\nReply with a single JSON object that matches this JSON schema, and "
            f"nothing else:\n{json.dumps(strict, separators=(',', ':'))}")}]
        started = time.monotonic()
        for attempt in range(2):
            response = self._create(stage, messages, effort, response_format=response_format)
            text = response.choices[0].message.content or ""
            try:
                parsed = schema.model_validate_json(json_text(text))
            except (ValidationError, ValueError) as e:
                if attempt:
                    raise LLMError(f"{stage}: output did not match the schema: {e}") from e
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": "That reply did not match the schema "
                     f"({str(e)[:1500]}). Reply with the corrected JSON object only."}]
                continue
            self._log(stage, prompt_name, prompt, effort, response, started,
                      output=parsed.model_dump(mode="json"), attempts=attempt + 1)
            return parsed
        raise AssertionError("unreachable")

    # Internals

    def _create(self, stage: str, messages: list[dict], effort: str, extra: dict | None = None,
                **kwargs):
        if self.budget:
            self.budget.check()
        limit, message = self.clock.call_limit(stage) if self.clock else (None, "")
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": self.system}, *messages],
                max_tokens=MAX_TOKENS,
                extra_body={
                    "reasoning": {"effort": EFFORTS.get(effort, "medium")},
                    # Only route to providers that support every parameter we send
                    # (tools, response_format).
                    "provider": {"require_parameters": True},
                    **(extra or {}),
                },
                **({"timeout": limit} if limit else {}),
                **kwargs,
            )
        except openai.APITimeoutError as e:
            raise TimeLimitExceeded(message) from e
        if self.budget and getattr(response, "usage", None):
            self.budget.record(getattr(response, "model", self.model), [response.usage.model_dump()])
        if not response.choices:
            raise LLMError(f"{stage}: the API returned no choices")
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise LLMError(f"{stage}: output hit max_tokens before it was complete")
        if choice.finish_reason == "content_filter" or choice.message.refusal:
            raise LLMRefusal(f"{stage}: the model declined ({choice.message.refusal or 'content filter'})")
        return response

    def _log(self, stage, prompt_name, prompt, effort, response, started, output, **more) -> None:
        usage = response.usage.model_dump() if getattr(response, "usage", None) else None
        self.log({
            "stage": stage,
            "prompt": prompt_name,
            "model": getattr(response, "model", self.model),
            "provider": "openrouter",
            "effort": effort,
            "input_sha256": hashlib.sha256(prompt.encode()).hexdigest()[:16],
            "stop_reason": response.choices[0].finish_reason,
            "request_id": getattr(response, "id", None),
            "usage": [usage],
            "seconds": round(time.monotonic() - started, 1),
            "output": output,
            **more,
        })
