"""Dry run: the whole pipeline with a fake Claude client, to see what a real run would use.

Every request goes through the real ClaudeLLM (token counting, call and spending caps, cost
lines), but nothing is sent to the API and no key is needed. The fake client answers with the
canned results and generated PDFs the tests use (tests/fakes.py) and reports worst-case usage
within the caps: every allowed web search and page fetch used and re-read at each step, the
longest risk section read, and max_tokens of output on every call.

  erm-bench run "City Developments" --dry-run
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace as NS

from .llm import (
    MAX_CALLS_PER_RUN,
    MAX_PROMPT_TOKENS,
    MAX_TOKENS,
    estimate_tokens,
    research_input_estimate,
)
from .pipeline import Pipeline, make_llm
from .schemas import Request
from .standard_setup import REPO_ROOT, StandardSetup

TOKENS_PER_PAGE = 1000   # a dense annual-report page, for the worst-case risk section

# Company names in the canned documents, and the ids the pipeline gives them.
EXTRACT_KEYS = [("Alpha Land", "P1"), ("Beta Properties", "P2"), ("CDL", "client")]


class _Stream:
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


class DryRunClient:
    """Stands in for anthropic.Anthropic. It has no count_tokens, so ClaudeLLM estimates prompt
    sizes locally, as it does when the counting endpoint is unavailable."""

    def __init__(self, fake_llm, setup: StandardSetup):
        self.fake = fake_llm
        self.max_risk_pages = setup.method["documents"]["max_risk_pages"]
        self.beta = NS(messages=NS(stream=self._stream))

    def with_options(self, **_):
        return self

    def _stream(self, *, model, system, messages, max_tokens, tools=None, output_format=None,
                **_):
        prompt = messages[0]["content"]
        prompt_tokens = estimate_tokens(system + repr(messages))
        usage = {"input_tokens": prompt_tokens, "output_tokens": max_tokens}
        content, parsed = [], None
        if tools:   # research: every allowed search and fetch used
            searches = tools[0]["max_uses"]
            fetch = tools[1] if len(tools) > 1 else {"max_uses": 0, "max_content_tokens": 0}
            usage["input_tokens"] = research_input_estimate(
                prompt_tokens, searches, fetch["max_uses"], fetch["max_content_tokens"])
            usage["server_tool_use"] = {"web_search_requests": searches}
            content = [NS(type="text", text="Research notes.", citations=[
                NS(url="https://src.test/notes", title="", cited_text="x")])]
        else:
            stage = ""
            if output_format.__name__ == "ExtractionDraft":   # the longest risk section allowed
                stage = "extract:" + next(k for name, k in EXTRACT_KEYS if name in prompt)
                usage["input_tokens"] = prompt_tokens + self.max_risk_pages * TOKENS_PER_PAGE
            parsed = self.fake.structured(stage=stage, prompt_name="", prompt=prompt,
                                          schema=output_format, effort="")
        return _Stream(NS(content=content, stop_reason="end_turn", stop_details=None,
                          parsed_output=parsed, model=model, usage=NS(to_dict=lambda: usage)))


def dry_run(request: Request, until: str, advance: Callable[[Pipeline, str], None]) -> int:
    setup = StandardSetup.load()
    if setup.provider != "anthropic":
        print(f"The dry run fakes the Claude client; the provider is '{setup.provider}'.",
              file=sys.stderr)
        return 1
    sys.path.insert(0, str(REPO_ROOT))   # the canned answers and documents live with the tests
    from tests import fakes

    fake = fakes.FakeLLM()
    made = []

    def factory(s: StandardSetup, store):
        made.append(make_llm(s, store, client=DryRunClient(fake, s)))
        return made[-1]

    with tempfile.TemporaryDirectory() as tmp:
        p = Pipeline.start(request, setup, llm_factory=factory, runs_dir=Path(tmp),
                           fetch=fakes.fake_fetch, progress=lambda m: print(f"… {m}", file=sys.stderr))
        advance(p, until)
        print_estimate(made[-1], setup)
    return 0


def print_estimate(llm, setup: StandardSetup) -> None:
    budget = llm.budget
    print(f"\nDRY RUN: no API calls were made. Model {setup.model}; worst case within the caps.\n")
    head = f"{'step':<30}{'calls':>6}{'prompt sent':>13}{'billed input':>14}{'output':>9}" \
           f"{'searches':>10}{'cost':>10}"
    print(head)
    print("-" * len(head))
    total = {"calls": 0, "prompt_tokens": 0, "input_tokens": 0, "output_tokens": 0,
             "searches": 0, "usd": 0.0}
    for stage, s in llm.steps.items():
        print(f"{stage:<30}{s['calls']:>6}{s['prompt_tokens']:>13,}{s['input_tokens']:>14,}"
              f"{s['output_tokens']:>9,}{s['searches']:>10}{'$' + format(s['usd'], '.4f'):>10}")
        for key in total:
            total[key] += s[key]
    print("-" * len(head))
    print(f"{'total':<30}{total['calls']:>6}{total['prompt_tokens']:>13,}"
          f"{total['input_tokens']:>14,}{total['output_tokens']:>9,}{total['searches']:>10}"
          f"{'$' + format(total['usd'], '.4f'):>10}")
    biggest = max((s["prompt_tokens"] / s["calls"] for s in llm.steps.values()), default=0)
    print(f"\nCaps: {MAX_CALLS_PER_RUN} calls (hard) and {budget.max_calls} (budget); "
          f"${budget.max_usd:.2f} per run; {budget.max_searches} web searches; "
          f"{MAX_PROMPT_TOKENS:,} tokens per prompt (largest sent here: ~{biggest:,.0f}); "
          f"{MAX_TOKENS:,} max output tokens per call.")
    print("'prompt sent' is measured on the real prompt templates with the test data (risk "
          "sections there are short). 'billed input' adds what the API would read on top: web "
          f"results re-read at each search step, {setup.method['documents']['max_risk_pages']}-page "
          f"risk sections. Output assumes every call writes its max_tokens. A real run is "
          "very likely cheaper; a research call can make extra requests if the API pauses it.")
