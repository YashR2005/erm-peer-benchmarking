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

from .conftest import _without_peer_cap
from .fakes import FakeLLM, fake_fetch


class SlowFakeLLM(FakeLLM):
    """Adds a pause per call so progress screens are visible."""

    def __init__(self, delay: float):
        super().__init__()
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
    llm = SlowFakeLLM(args.delay)
    # The browser tests cover three or more peer candidates: no trial peer cap.
    StandardSetup.load = classmethod(_without_peer_cap)
    app = create_app(StandardSetup.load(), runs_dir=runs_dir, llm_factory=lambda s, store: llm,
                     fetch=fake_fetch, static_dir=None)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
