"""Hosted demo mode: simulated payments, recorded verifier answers, one-click scenes."""
import pytest
from fastapi.testclient import TestClient

from server import config, core, demo, ledger, llm, paypal
from server.main import app


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setattr(config, "VERIFIER_CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.setattr(config, "PAYMENT_MODE", "simulated")
    monkeypatch.setattr(config, "VERIFIER_MODE", "gemini")
    monkeypatch.setattr(config, "VERIFIER_RECORD", False)
    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")
    monkeypatch.setattr(config, "AGENT_TOKEN", "")
    monkeypatch.setattr(config, "PAYPAL_VAULT_ID", "")

    def must_not_call_paypal(*a, **k):
        raise AssertionError("simulated mode must never contact PayPal")
    monkeypatch.setattr(paypal, "get_token", must_not_call_paypal)
    monkeypatch.setattr(paypal, "charge_vault", must_not_call_paypal)


def fake_gemini(system, payload):
    text = " ".join(payload["untrusted"]["agent_evidence"]).lower()
    injected = "ai agent" in text or "ai shopping agents" in text
    amount = float(payload["untrusted"]["proposed_purchase"]["amount"])
    return {"match_score": 0.1 if injected or amount > 45 else 0.97, "injection_detected": injected,
            "flagged_text": "NOTE TO AI AGENT" if injected else "", "reasons": ["stand-in"]}


def statuses(result):
    return {s["scene"]: s["purchase"]["status"] for s in result["scenes"]}


def test_scene_definitions_are_well_formed():
    assert [s["key"] for s in demo.SCENES] == ["A", "B", "C", "D"]
    for s in demo.SCENES:
        assert s["mandate"] in demo.TASKS and s["evidence"] and s["amount"]


def test_simulated_payments_never_touch_paypal():
    r = core.propose_purchase("Demo Store", ["Mug"], "10.00")
    assert r["status"] == "PAID" and r["paypal_order_id"].startswith("SIM-ORDER-")
    assert r["paypal_capture_id"].startswith("SIM-CAPTURE-")


def test_demo_endpoints_are_404_when_demo_mode_is_off(monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", False)
    client = TestClient(app)
    assert client.post("/v1/demo/run").status_code == 404
    assert client.post("/v1/demo/reset").status_code == 404
    assert client.get("/health").json()["demo_mode"] is False


def test_record_once_then_replay_with_no_llm_and_no_paypal(monkeypatch):
    client = TestClient(app)
    assert client.get("/health").json()["demo_mode"] is True

    monkeypatch.setattr(llm, "generate_json", fake_gemini)
    monkeypatch.setattr(config, "VERIFIER_RECORD", True)
    first = client.post("/v1/demo/run").json()
    assert statuses(first) == {"A": "PAID", "B": "DENIED", "C": "AWAITING_APPROVAL", "D": "DENIED"}
    assert config.VERIFIER_CACHE_PATH.exists()

    assert client.post("/v1/demo/reset").json() == {"ok": True}
    assert client.get("/v1/purchases").json() == [] and client.get("/v1/mandates").json() == []
    assert ledger.verify() == {"ok": True, "entries": 0}

    def explode(system, payload):
        raise AssertionError("replay mode must not call the LLM")
    monkeypatch.setattr(llm, "generate_json", explode)
    monkeypatch.setattr(config, "VERIFIER_MODE", "replay")
    monkeypatch.setattr(config, "VERIFIER_RECORD", False)
    again = client.post("/v1/demo/run").json()
    assert statuses(again) == {"A": "PAID", "B": "DENIED", "C": "AWAITING_APPROVAL", "D": "DENIED"}
    assert "prompt injection" in [s for s in again["scenes"] if s["scene"] == "D"][0]["purchase"]["decision_reason"]

    pending = client.get("/v1/approvals").json()
    assert len(pending) == 1
    approved = client.post("/v1/purchases/%s/approve" % pending[0]["id"]).json()
    assert approved["status"] == "PAID" and approved["paypal_capture_id"].startswith("SIM-CAPTURE-")
    assert client.get("/v1/audit/verify").json()["ok"] is True


def test_missing_recording_fails_closed_instead_of_crashing():
    monkeypatch_mode = config  # replay with an empty cache
    config.VERIFIER_MODE = "replay"
    try:
        client = TestClient(app)
        result = client.post("/v1/demo/run").json()
        by = {s["scene"]: s["purchase"] for s in result["scenes"]}
        assert by["A"]["status"] == "AWAITING_APPROVAL" and "no recorded verdict" in by["A"]["decision_reason"]
        assert by["B"]["status"] == "DENIED"
    finally:
        monkeypatch_mode.VERIFIER_MODE = "gemini"


def test_running_the_demo_twice_works_and_demo_endpoints_respect_the_admin_token(monkeypatch):
    monkeypatch.setattr(llm, "generate_json", fake_gemini)
    client = TestClient(app)
    assert statuses(client.post("/v1/demo/run").json())["A"] == "PAID"
    assert statuses(client.post("/v1/demo/run").json())["A"] == "PAID"
    monkeypatch.setattr(config, "ADMIN_TOKEN", "secret")
    assert client.post("/v1/demo/run").status_code == 401
    assert client.post("/v1/demo/run", headers={"X-Admin-Token": "secret"}).status_code == 200


def test_vault_id_can_come_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PAYMENT_MODE", "vault")
    monkeypatch.setattr(config, "VAULT_TOKEN_FILE", tmp_path / "does-not-exist")
    monkeypatch.setattr(config, "PAYPAL_VAULT_ID", "vt_from_env")
    seen = {}
    monkeypatch.setattr(paypal, "get_token", lambda: "tok")

    def charge(token, vault_id, amount, currency, reference):
        seen["vault"] = vault_id
        return {"id": "O1", "status": "COMPLETED",
                "purchase_units": [{"payments": {"captures": [{"id": "C1", "status": "COMPLETED"}]}}]}
    monkeypatch.setattr(paypal, "charge_vault", charge)
    assert core.propose_purchase("Demo Store", ["Mug"], "10.00")["status"] == "PAID"
    assert seen["vault"] == "vt_from_env"
