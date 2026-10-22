"""The real API with the fake model and fake documents, for browser tests.

  uv run python -m tests.e2e_server --port 8010 [--delay 0.4]

Runs live in a fresh temporary folder, so tests never touch runs/.
"""

from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

import uvicorn

from erm_bench.api import create_app
from erm_bench.standard_setup import StandardSetup

from .fakes import LoggedFakeLLM, fake_fetch


class SlowFakeLLM(LoggedFakeLLM):
    """Adds a pause per call so progress screens are visible. Calls are logged to the run's
    llm_calls.jsonl like the real client, so the Steps view has times and costs to show."""

    def __init__(self, delay: float, log=None):
        super().__init__(log, seconds=delay)
        self.delay = delay

    def research(self, **kwargs):
        time.sleep(self.delay)
        return super().research(**kwargs)

    def structured(self, **kwargs):
        time.sleep(self.delay)
        return super().structured(**kwargs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--delay", type=float, default=0.3)
    args = parser.parse_args()
    runs_dir = Path(tempfile.mkdtemp(prefix="erm-e2e-"))
    app = create_app(StandardSetup.load(), runs_dir=runs_dir, llm_factory=lambda s, store: SlowFakeLLM(
                         args.delay, store.log_llm_call if store else None),
                     fetch=fake_fetch, static_dir=None)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
