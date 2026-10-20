from pathlib import Path

from erm_bench.chat import ChatSession

from .fakes import FakeLLM, fake_fetch, findings, scope


def test_chat_drives_the_fixed_stages(tmp_path, setup):
    llm = FakeLLM()
    chat = ChatSession(setup, runs_dir=tmp_path, llm_factory=lambda s, store: llm, fetch=fake_fetch)
    progress: list[str] = []

    replies = chat.handle("Benchmark City Developments for FY2025 with 2 peers", progress.append)
    assert chat.status == "awaiting_scope"
    assert "Benchmarking **City Developments** for FY2025" in replies[0].text
    peer_table = next(r.table for r in replies if r.caption and r.caption.startswith("Peer"))
    assert [row["Use"] for row in peer_table] == ["✓", "✓", ""]
    assert any("Light disclosure scan" in m for m in progress)

    llm.scope_commands.append(scope("deselect", ["Beta"], reply="Dropping Beta Properties."))
    replies = chat.handle("drop beta")
    assert replies[0].text == "Dropping Beta Properties."
    assert [c.selected for c in chat.pipeline.peers.candidates] == [True, False, False]

    replies = chat.handle("go")          # matched without an LLM call
    assert chat.status == "awaiting_findings"
    assert any(r.caption == "Risk coverage" for r in replies)
    assert any(r.caption == "Category structure" for r in replies)

    llm.findings_commands.append(findings("remove_theme", ["fx"]))
    replies = chat.handle("remove the FX theme")
    matrix = next(r.table for r in replies if r.caption == "Risk coverage")
    assert all(row["Key"] != "fx" for row in matrix)

    llm.findings_commands.append(findings("remove_theme", ["unmapped"]))
    replies = chat.handle("remove the unmapped theme")
    assert replies[-1].text == "No theme matched unmapped."

    llm.findings_commands.append(findings("show", ["interest_rate"]))
    replies = chat.handle("evidence for interest rates")
    evidence = next(r.table for r in replies if r.caption == "Evidence")
    assert {row["Risk"] for row in evidence} == {"R-client-1", "R-P1-1"}

    replies = chat.handle("export")
    assert replies[0].file and Path(replies[0].file).exists()
    assert chat.status == "exported"

    # The same run can be reopened later.
    other = ChatSession(setup, runs_dir=tmp_path, llm_factory=lambda s, store: llm)
    replies = other.resume(chat.pipeline.store.run_id)
    assert any(r.caption == "Risk coverage" for r in replies)


def test_chat_asks_for_client_when_missing(tmp_path, setup):
    llm = FakeLLM()
    original = llm.structured

    def no_client(**kwargs):
        draft = original(**kwargs)
        return draft.model_copy(update={"client_name": None})

    llm.structured = no_client
    chat = ChatSession(setup, runs_dir=tmp_path, llm_factory=lambda s, store: llm)
    replies = chat.handle("hello")
    assert chat.pipeline is None and "Which company" in replies[0].text
