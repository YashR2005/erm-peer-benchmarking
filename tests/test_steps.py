"""The Steps view: derived from the files of a run, read-only."""

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from erm_bench.api import create_app
from erm_bench.pipeline import Pipeline
from erm_bench.schemas import Request
from erm_bench.standard_setup import REPO_ROOT
from erm_bench.steps import list_steps, step_detail
from erm_bench.store import RunStore

from .fakes import fake_fetch, logged_factory


def make_run(tmp_path, setup, until="export"):
    factory, instances = logged_factory()
    p = Pipeline.start(Request(client_name="City Developments", reporting_year=2025, peer_count=2),
                       setup, llm_factory=factory, runs_dir=tmp_path, fetch=fake_fetch, max_workers=1)
    p.research()
    if until != "research":
        p.lock_scope()
    if until in ("findings", "export"):
        p.analyse()
    if until == "export":
        p.export()
    return p, instances


def by_id(listing):
    return {s["id"]: s for s in listing["steps"]}


def test_steps_of_a_full_run(tmp_path, setup):
    p, _ = make_run(tmp_path, setup)
    listing = list_steps(p.store, setup)
    steps = by_id(listing)
    assert list(steps) == [
        "request", "profile", "peers", "documents:client", "documents:P1", "documents:P2",
        "documents:P3", "scope", "extraction:client", "extraction:P1", "extraction:P2",
        "context_risks", "themes", "review", "export"]
    assert {s["status"] for s in steps.values()} == {"done"}
    assert [g["key"] for g in listing["groups"]] == ["research", "scope", "analysis", "review", "export"]
    assert steps["extraction:client"]["summary"] == "4 risks, 3 verified"
    assert steps["themes"]["summary"].endswith("1 'Not mapped (review)'")
    assert steps["profile"]["cost_usd"] > 0 and steps["profile"]["seconds"] == 1.0   # 2 calls x 0.5 s
    assert steps["scope"]["seconds"] is None                  # not a model step: not recorded
    assert listing["live_step"] == "export" and not listing["running"]
    assert steps["extraction:P1"]["depends_on"] == ["scope", "documents:P1"]


def test_status_for_stopped_running_and_failed(tmp_path, setup):
    p, _ = make_run(tmp_path, setup, until="scope")           # locked, analysis not run
    assert p.store.status == "analysing"
    idle = by_id(list_steps(p.store, setup))
    assert idle["scope"]["status"] == "done"
    assert idle["extraction:client"]["status"] == "stopped"   # no job known: never "running"
    assert idle["themes"]["status"] == "not started"          # its inputs aren't done yet

    running = by_id(list_steps(p.store, setup, {"running": True, "kind": "analysis"}))
    assert running["extraction:client"]["status"] == "running"
    assert running["context_risks"]["status"] == "running"
    assert running["themes"]["status"] == "not started"

    user_stop = by_id(list_steps(p.store, setup, {"running": False, "stopped": True, "kind": "analysis"}))
    assert user_stop["extraction:P1"]["status"] == "stopped"
    assert user_stop["extraction:P1"]["reason"] == "Stopped by you"

    failed = by_id(list_steps(p.store, setup, {"running": False, "error": "boom", "kind": "analysis"}))
    assert failed["extraction:P1"]["status"] == "failed" and failed["extraction:P1"]["reason"] == "boom"


def test_running_research_marks_unfinished_steps(tmp_path, setup):
    p, _ = make_run(tmp_path, setup, until="research")
    p.store.set_status("researching")                         # as if research was still going
    (p.store.dir / "stages" / "documents.json").unlink()
    steps = by_id(list_steps(p.store, setup, {"running": True, "kind": "research"}))
    assert steps["profile"]["status"] == "done" and steps["peers"]["status"] == "done"
    assert steps["documents:client"]["status"] == "running"
    assert steps["scope"]["status"] == "not started"


def test_extraction_detail_has_quote_page_and_verification(tmp_path, setup):
    p, _ = make_run(tmp_path, setup)
    d = step_detail(p.store, setup, "extraction:client")
    risks = {r["title"]: r for r in d["output"]["risks"]}
    cyber = risks["Cybersecurity risk"]
    assert cyber["page"] == 5 and cyber["verified"] is True and cyber["score"] == 100
    assert "cyber attack" in cyber["quote"]
    assert risks["Bribery risk"]["verified"] is False         # invented quote
    assert any("not verified" in w for w in d["warnings"])
    assert d["inputs"][0]["label"] == "Document" and "Pages read" in [r["label"] for r in d["inputs"]]
    assert d["cost"]["calls"] == 1 and d["calls"][0]["stage"] == "extract:client"
    assert d["calls"][0]["model"] == "fake-model" and d["calls"][0]["prompt"]


def test_profile_detail_shows_sources_and_unsourced_facts(tmp_path, setup):
    p, _ = make_run(tmp_path, setup)
    d = step_detail(p.store, setup, "profile", raw=True)
    facts = d["output"]["facts"]
    assert [bool(f["sources"]) for f in facts] == [True, True, False]
    assert facts[0]["sources"][0]["url"].startswith("https://src.test/")
    assert "profile" in d["raw"] and d["cost"]["searches"] == 2
    assert "raw" not in step_detail(p.store, setup, "profile")   # raw only on request
    assert step_detail(p.store, setup, "nope") is None


def test_my_review_actions_appear_under_the_right_step(tmp_path, setup):
    factory, _ = logged_factory()
    p = Pipeline.start(Request(client_name="City Developments", reporting_year=2025, peer_count=2),
                       setup, llm_factory=factory, runs_dir=tmp_path, fetch=fake_fetch, max_workers=1)
    p.research()
    p.select(["gamma"])
    p.select(["P3"], selected=False)
    p.mark_na("D-P3-1", "REIT; no annual report PDF")
    p.lock_scope()
    p.analyse()
    p.remove_themes(["fx"])
    p.reject_evidence(["R-client-3"])
    p.add_remark("Both peers lack Sustainability.")
    p.export()

    def actions(step_id):
        return [a["action"] for a in step_detail(p.store, setup, step_id)["actions"]]

    assert actions("peers") == ["select", "deselect"]
    assert actions("documents:P3") == ["mark_na"]
    assert actions("scope") == ["lock_scope"]
    assert actions("review") == ["remove_theme", "reject_evidence", "add_remark"]
    assert actions("export") == ["export"]
    assert actions("profile") == []
    assert by_id(list_steps(p.store, setup))["review"]["edits"] == 3


def test_long_model_output_is_truncated_unless_asked(tmp_path, setup):
    p, _ = make_run(tmp_path, setup, until="research")
    log = p.store.dir / "llm_calls.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    first = next(r for r in rows if r["stage"] == "profile")
    first["output"] = {"text": "x" * 5000, "citations": []}
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    call = step_detail(p.store, setup, "profile")["calls"][0]
    assert call["output_truncated"] and len(call["output"]) == 2000 and call["output_chars"] > 5000
    full = step_detail(p.store, setup, "profile", full=True)["calls"][0]
    assert not full["output_truncated"] and len(full["output"]["text"]) == 5000


def digest(folder: Path) -> dict:
    return {str(f.relative_to(folder)): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(folder.rglob("*")) if f.is_file()}


def test_old_run_in_runs_folder_is_read_without_writing(setup):
    runs = REPO_ROOT / "runs"
    candidates = sorted(runs.glob("*/stages/extractions.json")) if runs.exists() else []
    if not candidates:
        pytest.skip("no finished run in runs/")
    folder = candidates[-1].parent.parent
    before = digest(folder)
    store = RunStore(folder)
    listing = list_steps(store, setup)
    for s in listing["steps"]:
        assert step_detail(store, setup, s["id"], raw=True, full=True)["id"] == s["id"]
    steps = by_id(listing)
    assert steps["profile"]["status"] == "done" and steps["themes"]["status"] == "done"
    assert steps["profile"]["seconds"] is not None
    assert digest(folder) == before                            # nothing written into the run


def test_a_bare_old_run_gives_a_sensible_list(tmp_path, setup):
    store = RunStore.create("Old Co", {"setup_version": "v1", "request": {"client_name": "Old Co"}},
                            runs_dir=tmp_path)
    steps = by_id(list_steps(store, setup))
    assert steps["request"]["status"] == "not started"        # no request.json in this old folder
    assert steps["profile"]["status"] == "not started" and steps["profile"]["cost_usd"] is None


def test_steps_over_http(tmp_path, setup):
    p, _ = make_run(tmp_path, setup)
    factory, _ = logged_factory()
    client = TestClient(create_app(setup, runs_dir=tmp_path, llm_factory=factory, fetch=fake_fetch,
                                   static_dir=None))
    rid = p.store.run_id
    listing = client.get(f"/api/runs/{rid}/steps").json()
    assert listing["status"] == "exported" and len(listing["steps"]) == 15
    detail = client.get(f"/api/runs/{rid}/steps/extraction:P1").json()
    assert detail["status"] == "done" and detail["output"]["kind"] == "extraction"
    assert client.get(f"/api/runs/{rid}/steps/nope").status_code == 404
    assert client.get("/api/runs/missing/steps").status_code == 404
