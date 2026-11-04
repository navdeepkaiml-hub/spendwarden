"""Tests for the Day 2 walking skeleton. PayPal is mocked, so these never touch the network."""
import asyncio
import sqlite3

import pytest

from server import config, core, ledger, paypal


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.sqlite3"))
    vault = tmp_path / ".vault_token"
    vault.write_text("vt_test_123")
    monkeypatch.setattr(config, "VAULT_TOKEN_FILE", vault)
    monkeypatch.setattr(config, "PAYMENT_MODE", "vault")
    monkeypatch.setattr(paypal, "get_token", lambda: "tok")


def fake_vault_charge(status="COMPLETED"):
    def _charge(token, vault_id, amount, currency, reference):
        assert vault_id == "vt_test_123"
        return {
            "id": "ORDER1",
            "status": status,
            "purchase_units": [{"payments": {"captures": [{"id": "CAP1", "status": status}]}}],
        }
    return _charge


def test_vault_purchase_is_paid_and_ledgered(monkeypatch):
    monkeypatch.setattr(paypal, "charge_vault", fake_vault_charge())
    result = core.propose_purchase("Demo Store", ["Mug"], "18", "usd", "gift")
    assert result["status"] == "PAID"
    assert result["amount"] == "18.00" and result["currency"] == "USD"
    assert result["paypal_capture_id"] == "CAP1"
    assert result["decision"] == "ALLOW"
    assert ledger.verify() == {"ok": True, "entries": 3}  # proposed, decided, payment_result
    assert core.get_purchase(result["id"])["items"] == ["Mug"]
    assert len(core.list_purchases()) == 1


def test_invalid_input_is_rejected():
    for amount in ["abc", "-5", "0", "NaN", "Infinity"]:
        with pytest.raises(ValueError):
            core.propose_purchase("Demo Store", [], amount)
    with pytest.raises(ValueError):
        core.propose_purchase("", [], "5")
    with pytest.raises(ValueError):
        core.propose_purchase("Demo Store", [], "5", currency="DOLLARS")
    assert core.list_purchases() == []


def test_paypal_failure_is_recorded_not_raised(monkeypatch):
    def boom(*a, **k):
        raise paypal.PayPalError("Vault charge failed (HTTP 422)")
    monkeypatch.setattr(paypal, "charge_vault", boom)
    result = core.propose_purchase("Demo Store", ["x"], "10")
    assert result["status"] == "PAYMENT_FAILED"
    assert "HTTP 422" in result["error"]
    assert ledger.verify()["ok"] is True


def test_missing_vault_token_is_a_clear_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VAULT_TOKEN_FILE", tmp_path / "nope")
    result = core.propose_purchase("Demo Store", ["x"], "10")
    assert result["status"] == "PAYMENT_FAILED"
    assert "paypal_vault_spike.py" in result["error"]


def test_incomplete_capture_is_not_marked_paid(monkeypatch):
    monkeypatch.setattr(paypal, "charge_vault", fake_vault_charge(status="PENDING"))
    assert core.propose_purchase("Demo Store", ["x"], "10")["status"] == "PAYMENT_FAILED"


def test_approval_mode_flow(monkeypatch):
    monkeypatch.setattr(config, "PAYMENT_MODE", "approval")
    monkeypatch.setattr(paypal, "create_order_for_approval", lambda t, a, c, r: ("ORD9", "https://sandbox/approve"))
    first = core.propose_purchase("Demo Store", ["x"], "12.50")
    assert first["status"] == "AWAITING_PAYER" and first["approve_url"] == "https://sandbox/approve"

    monkeypatch.setattr(paypal, "capture", lambda t, o: {
        "id": o, "status": "COMPLETED",
        "purchase_units": [{"payments": {"captures": [{"id": "CAP9", "status": "COMPLETED"}]}}]})
    done = core.capture_approved(first["id"])
    assert done["status"] == "PAID" and done["paypal_capture_id"] == "CAP9"
    with pytest.raises(ValueError):
        core.capture_approved(first["id"])  # already paid


def test_unknown_payment_mode_fails_closed(monkeypatch):
    monkeypatch.setattr(config, "PAYMENT_MODE", "banana")
    assert core.propose_purchase("Demo Store", ["x"], "10")["status"] == "PAYMENT_FAILED"


def test_ledger_detects_tampering(monkeypatch):
    monkeypatch.setattr(paypal, "charge_vault", fake_vault_charge())
    core.propose_purchase("Demo Store", ["x"], "10")
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("UPDATE audit SET payload = replace(payload, '10.00', '1.00') WHERE seq = 1")
    conn.commit()
    conn.close()
    result = ledger.verify()
    assert result["ok"] is False and result["broken_at"] == 1


def test_rest_api(monkeypatch):
    from fastapi.testclient import TestClient
    from server.main import app

    monkeypatch.setattr(paypal, "charge_vault", fake_vault_charge())
    client = TestClient(app)
    assert client.get("/health").json()["ok"] is True
    r = client.post("/v1/purchases", json={"merchant": "Demo Store", "items": ["Mug"], "amount": 18.0})
    assert r.status_code == 200 and r.json()["status"] == "PAID"
    pid = r.json()["id"]
    assert client.get("/v1/purchases/" + pid).json()["id"] == pid
    assert client.get("/v1/purchases/pur_missing").status_code == 404
    assert client.post("/v1/purchases", json={"merchant": "S", "amount": "oops"}).status_code == 422
    assert client.get("/v1/audit/verify").json()["ok"] is True


def test_mcp_tools_registered_and_callable(monkeypatch):
    from server import mcp_server

    tools = asyncio.run(mcp_server.mcp.list_tools())
    assert {"propose_purchase", "get_purchase_status"} <= {t.name for t in tools}
    monkeypatch.setattr(paypal, "charge_vault", fake_vault_charge())
    out = mcp_server.propose_purchase("Demo Store", ["Mug"], "18.00")
    assert out["status"] == "PAID"
    assert mcp_server.propose_purchase("Demo Store", ["Mug"], "oops")["status"] == "REJECTED_INVALID"
    assert mcp_server.get_purchase_status("pur_nope")["status"] == "NOT_FOUND"
