"""Time limits and the Stop button.

Limits: one per stage (shared by the calls in it) and one per model call. A
call that would start after its stage's limit fails at once; a call that runs
past its limit is cut off, which also stops it being billed. A stop request
works the same way. Stages save what finished, so resuming continues from
where a limit or a stop left them.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager


class TimeLimitExceeded(RuntimeError):
    pass


class StopRequested(RuntimeError):
    """The consultant pressed Stop."""


def minutes(seconds: float) -> str:
    return f"{seconds / 60:g} min" if seconds >= 60 else f"{seconds:g} s"


class Clock:
    def __init__(self, per_call: float | None = None, stop: threading.Event | None = None):
        self.per_call = per_call
        self.stop = stop
        self.stage_name: str | None = None
        self.stage_limit: float | None = None
        self.stage_end: float | None = None

    @contextmanager
    def stage(self, name: str, seconds: float | None) -> Iterator[None]:
        self.stage_name, self.stage_limit = name, seconds
        self.stage_end = time.monotonic() + seconds if seconds else None
        try:
            yield
        finally:
            self.stage_name = self.stage_limit = self.stage_end = None

    def _stage_message(self) -> str:
        return (f"The {self.stage_name} stage reached its {minutes(self.stage_limit)} time limit. "
                "What finished is saved; Try again continues from where it stopped. The limit is "
                "under 'time_limits' in setup/v1/method.yaml.")

    def check(self) -> None:
        """Raise if Stop was pressed or the stage is past its time limit."""
        if self.stop is not None and self.stop.is_set():
            raise StopRequested("Stopped. What finished is saved; Resume continues from here.")
        if self.stage_end is not None and time.monotonic() >= self.stage_end:
            raise TimeLimitExceeded(self._stage_message())

    def call_limit(self, what: str) -> tuple[float | None, str]:
        """Seconds the next call may run, and the message to raise if it runs over."""
        self.check()
        left = self.stage_end - time.monotonic() if self.stage_end is not None else None
        if self.per_call and (left is None or self.per_call <= left):
            return self.per_call, (f"{what}: the model call ran longer than {minutes(self.per_call)} "
                                   "and was stopped. Try again, or raise 'per_call' under "
                                   "'time_limits' in setup/v1/method.yaml.")
        return left, self._stage_message()
