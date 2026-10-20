import time

import pytest
from fastapi.testclient import TestClient

from erm_bench.api import create_app

from .fakes import DOCS, FakeLLM, fake_fetch, findings


@pytest.fixture
def api(tmp_path, setup):
    llm = FakeLLM()
    app = create_app(setup, runs_dir=tmp_path, llm_factory=lambda s, store: llm, fetch=fake_fetch,
                     static_dir=None)
    return TestClient(app), llm


def wait(client: TestClient, run_id: str) -> dict:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        state = client.get(f"/api/runs/{run_id}").json()
        if not (state["job"] and state["job"]["running"]):
            assert not state["job"] or not state["job"]["error"], state["job"]["error"]
            return state
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def start(client: TestClient) -> str:
    r = client.post("/api/runs", json={"client_name": "City Developments", "reporting_year": 2025,
                                       "peer_count": 2})
    assert r.status_code == 200
    return r.json()["run"]["run_id"]


def test_setup_and_health(api):
    client, _ = api
    assert client.get("/api/health").json()["ok"]
    setup = client.get("/api/setup").json()
    assert [b["key"] for b in setup["buckets"]] == ["large", "mid", "small"]
    assert setup["bucket_status"] == "provisional"
    assert len(setup["taxonomy"]) == 7


def test_full_run_over_http(api):
    client, llm = api
    run_id = start(client)
    state = wait(client, run_id)
    assert state["run"]["status"] == "awaiting_scope"
    assert state["profile"]["short_name"] == "CDL"
    assert [c["selected"] for c in state["peers"]["candidates"]] == [True, True, False]
    assert state["companies"]["client"] == {"availability": "good", "primary_doc_id": "D-client-1"}
    assert [p["message"] for p in state["job"]["progress"]][0].startswith("Profiling")

    # Checkpoint 1: add Gamma via an uploaded PDF, then lock.
    state = client.post(f"/api/runs/{run_id}/peers/P3", json={"selected": True}).json()
    assert state["peers"]["candidates"][2]["selected"]
    pdf = DOCS["https://sgx.test/ann/1/1_Gamma_REIT_Annual_Report_2025.pdf"]
    r = client.post(f"/api/runs/{run_id}/documents/upload", data={"company_id": "P3"},
                    files={"file": ("gamma-ar-2025.pdf", pdf, "application/pdf")})
    assert r.status_code == 200
    assert r.json()["companies"]["P3"]["availability"] == "good"
    assert client.post(f"/api/runs/{run_id}/documents/D-P3-1/na",
                       json={"reason": "REIT page, not a PDF"}).status_code == 200

    llm.extractions["P3"] = llm.extractions["P2"]
    client.post(f"/api/runs/{run_id}/lock")
    state = wait(client, run_id)
    assert state["run"]["status"] == "awaiting_findings"
    f = state["findings"]
    assert [c["id"] for c in f["companies"]] == ["client", "P1", "P2", "P3"]
    # P3 reuses Beta's quotes, which are not in Gamma's PDF: the verifier rejects them.
    assert f["stats"]["risks"] == 11 and f["stats"]["unverified"] == 3
    row = next(r for r in f["rows"] if r["theme"]["key"] == "cyber")
    assert row["cells"]["client"] == ["R-client-3"]
    assert any(g["type"] == "relevant_not_disclosed" and g["theme_key"] == "fx" for g in f["gaps"])
    assert f["structure"]["observations"]

    # Checkpoint 2 edits.
    f = client.patch(f"/api/runs/{run_id}/themes/fx", json={"removed": True}).json()["findings"]
    assert "fx" in [t["key"] for t in f["removed_themes"]]
    f = client.patch(f"/api/runs/{run_id}/themes/cyber",
                     json={"name": "Cyber and data", "category": "technology"}).json()["findings"]
    assert next(r for r in f["rows"] if r["theme"]["key"] == "cyber")["theme"]["name"] == "Cyber and data"
    f = client.post(f"/api/runs/{run_id}/evidence/R-P1-1", json={"rejected": True}).json()["findings"]
    assert next(r for r in f["risks"] if r["id"] == "R-P1-1")["rejected"]
    f = client.post(f"/api/runs/{run_id}/evidence/R-P1-1", json={"rejected": False}).json()["findings"]
    assert not next(r for r in f["risks"] if r["id"] == "R-P1-1")["rejected"]
    f = client.post(f"/api/runs/{run_id}/remarks", json={"text": "Add a People category."}).json()
    assert f["findings"]["remarks"] == ["Add a People category."]
    assert client.patch(f"/api/runs/{run_id}/themes/fx", json={"category": "nope"}).status_code == 400

    # Export and download.
    out = client.post(f"/api/runs/{run_id}/export").json()
    assert out["state"]["run"]["status"] == "exported"
    xlsx = client.get(out["url"])
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
    assert client.get(f"/api/runs/{run_id}/exports/..%2Frun.json").status_code == 404

    # The run list.
    runs = client.get("/api/runs").json()
    assert runs[0]["run_id"] == run_id and runs[0]["client"] == "CDL"
    assert runs[0]["peers"] == ["Alpha Land", "Beta Properties", "Gamma REIT"]


def test_chat_on_a_run(api):
    client, llm = api
    run_id = start(client)
    wait(client, run_id)
    client.post(f"/api/runs/{run_id}/chat", json={"message": "go"})
    state = wait(client, run_id)
    assert state["run"]["status"] == "awaiting_findings"
    assert [m["role"] for m in state["chat"]] == ["user", "assistant"]
    assert state["chat"][1]["text"].startswith("Scope locked.")

    llm.findings_commands.append(findings("remove_theme", ["fx"], reply="Removing FX."))
    client.post(f"/api/runs/{run_id}/chat", json={"message": "drop the fx theme"})
    state = wait(client, run_id)
    assert state["chat"][-1]["text"] == "Removing FX."
    assert "fx" in [t["key"] for t in state["findings"]["removed_themes"]]


def test_start_by_chat(api):
    client, _ = api
    r = client.post("/api/chat", json={"message": "Benchmark City Developments, 2 peers"}).json()
    state = wait(client, r["run_id"])
    assert state["run"]["status"] == "awaiting_scope"
    assert state["chat"][0]["text"] == "Benchmark City Developments, 2 peers"


def test_errors(api):
    client, _ = api
    assert client.get("/api/runs/nope").status_code == 404
    run_id = start(client)
    wait(client, run_id)
    client.post(f"/api/runs/{run_id}/peers/P1", json={"selected": False})
    client.post(f"/api/runs/{run_id}/peers/P2", json={"selected": False})
    assert client.post(f"/api/runs/{run_id}/lock").status_code == 400   # no peer selected
    assert client.post(f"/api/runs/{run_id}/peer-count", json={"count": 0}).status_code == 400


def test_stop_and_resume(tmp_path, setup):
    import time as _time

    class SlowLLM(FakeLLM):
        def research(self, **kwargs):
            _time.sleep(0.2)
            return super().research(**kwargs)

    llm = SlowLLM()
    client = TestClient(create_app(setup, runs_dir=tmp_path, llm_factory=lambda s, store: llm,
                                   fetch=fake_fetch, static_dir=None))
    run_id = start(client)
    _time.sleep(0.05)
    assert client.post(f"/api/runs/{run_id}/stop").status_code == 200
    state = wait(client, run_id)
    assert state["job"]["stopped"] and not state["job"]["error"]
    assert state["run"]["status"] == "researching"           # shown as stopped, with Resume

    client.post(f"/api/runs/{run_id}/resume")
    state = wait(client, run_id)
    assert state["run"]["status"] == "awaiting_scope" and not state["job"]["stopped"]
    assert [c for k, c in llm.calls if k == "research"].count("profile") == 1   # done once

    assert client.post(f"/api/runs/{run_id}/stop").status_code == 400          # nothing running
    r = client.post(f"/api/runs/{run_id}/documents/D-P1-1/na", json={"reason": "x"})
    assert next(d for d in r.json()["documents"] if d["id"] == "D-P1-1")["status"] == "na"
    r = client.post(f"/api/runs/{run_id}/documents/D-P1-1/restore")
    assert next(d for d in r.json()["documents"] if d["id"] == "D-P1-1")["status"] == "ok"
