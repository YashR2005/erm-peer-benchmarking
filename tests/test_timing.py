import time
from types import SimpleNamespace as NS

import pytest

from erm_bench.llm import ClaudeLLM
from erm_bench.pipeline import Pipeline
from erm_bench.schemas import Request
from erm_bench.timing import Clock, TimeLimitExceeded

from .fakes import FakeLLM, fake_fetch


def test_clock_limits():
    clock = Clock(per_call=240)
    assert clock.call_limit("x")[0] == 240                     # no stage running
    with clock.stage("research", 60):
        limit, message = clock.call_limit("x")
        assert 59 < limit <= 60 and "research stage" in message   # stage time left is shorter
    with clock.stage("research", 0.01):
        time.sleep(0.02)
        with pytest.raises(TimeLimitExceeded, match="0.01 s time limit"):
            clock.check()
    assert clock.stage_end is None                             # reset after the stage


class SlowStream:
    """Yields stream events slowly, like a long model answer."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        for _ in range(10):
            time.sleep(0.02)
            yield object()

    def get_final_message(self):
        raise AssertionError("should have been cut off")


def test_a_slow_call_is_cut_off():
    seen = {}
    client = NS(beta=NS(messages=NS(stream=lambda **kw: SlowStream())))

    def with_options(timeout):
        seen["timeout"] = timeout
        return client

    client.with_options = with_options
    llm = ClaudeLLM("claude-sonnet-5-5", "system", client=client, clock=Clock(per_call=0.05))
    with pytest.raises(TimeLimitExceeded, match="ran longer than"):
        llm.structured(stage="extract:P1", prompt_name="x", prompt="p", schema=NS, effort="high")
    assert seen["timeout"] == 0.05


def failing_once(llm: FakeLLM, method: str, stage: str):
    original = getattr(llm, method)
    state = {"failed": False}

    def wrapper(**kwargs):
        if kwargs["stage"] == stage and not state["failed"]:
            state["failed"] = True
            raise TimeLimitExceeded("The research stage reached its time limit.")
        return original(**kwargs)

    setattr(llm, method, wrapper)


def stages(llm: FakeLLM, kind: str) -> list[str]:
    return [stage for k, stage in llm.calls if k == kind]


def test_research_continues_where_a_time_limit_stopped_it(tmp_path, setup):
    llm = FakeLLM()
    failing_once(llm, "research", "disclosure:P2")
    p = Pipeline.start(Request(client_name="City Developments", peer_count=2), setup,
                       llm_factory=lambda s, store: llm, runs_dir=tmp_path, fetch=fake_fetch)
    with pytest.raises(TimeLimitExceeded):
        p.research()
    assert p.store.status == "researching"
    assert {d.company_id for d in p.documents} == {"client", "P1", "P3"}   # finished ones saved

    p.research()                                               # Try again
    assert p.store.status == "awaiting_scope"
    assert {d.company_id for d in p.documents} == {"client", "P1", "P2", "P3"}
    research = stages(llm, "research")
    assert research.count("profile") == 1 and research.count("peers") == 1   # not redone
    assert research.count("disclosure:P1") == 1 and research.count("disclosure:P2") == 1


def test_analysis_continues_where_a_time_limit_stopped_it(tmp_path, setup):
    llm = FakeLLM()
    failing_once(llm, "structured", "extract:P1")
    p = Pipeline.start(Request(client_name="City Developments", peer_count=2), setup,
                       llm_factory=lambda s, store: llm, runs_dir=tmp_path, fetch=fake_fetch)
    p.research()
    p.lock_scope()
    with pytest.raises(TimeLimitExceeded):
        p.analyse()
    assert p.store.status == "analysing"
    assert p.store.has("extract-client") and p.store.has("extract-P2")
    assert not p.store.has("extract-P1")

    p.analyse()                                                # Try again
    assert p.store.status == "awaiting_findings"
    structured = stages(llm, "structured")
    assert structured.count("extract:client") == 1 and structured.count("extract:P1") == 1
    assert structured.count("context_risks") == 1 and structured.count("mapping") == 1
    assert len(p.findings().rows) > 0


def test_stop_cuts_off_a_call_and_the_stage():
    import threading

    from erm_bench.timing import StopRequested

    stop = threading.Event()
    clock = Clock(per_call=240, stop=stop)
    clock.check()
    stop.set()
    with pytest.raises(StopRequested):
        clock.check()

    stop.clear()
    client = NS(beta=NS(messages=NS(stream=lambda **kw: SlowStream())))
    client.with_options = lambda timeout: client
    llm = ClaudeLLM("claude-sonnet-5-5", "system", client=client, clock=clock)
    threading.Timer(0.05, stop.set).start()          # Stop pressed while the answer streams
    started = time.monotonic()
    with pytest.raises(StopRequested):
        llm.structured(stage="extract:P1", prompt_name="x", prompt="p", schema=NS, effort="high")
    assert time.monotonic() - started < 0.15         # cut off within an event or two
