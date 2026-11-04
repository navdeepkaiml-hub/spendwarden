"""Tests for the policy engine, decisions and approvals. PayPal is mocked."""
import asyncio
import sqlite3

import pytest

from server import config, core, ledger, paypal, policy


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.sqlite3"))
    vault = tmp_path / ".vault_token"
    vault.write_text("vt_test_123")
    monkeypatch.setattr(config, "VAULT_TOKEN_FILE", vault)
    monkeypatch.setattr(config, "PAYMENT_MODE", "vault")
    monkeypatch.setattr(paypal, "get_token", lambda: "tok")


class FakePay:
    def __init__(self):
        self.calls = []

    def __call__(self, token, vault_id, amount, currency, reference):
        assert vault_id == "vt_test_123"
        self.calls.append(amount)
        return {
            "id": "ORDER%d" % len(self.calls),
            "status": "COMPLETED",
            "purchase_units": [{"payments": {"captures": [{"id": "CAP%d" % len(self.calls), "status": "COMPLETED"}]}}],
        }


@pytest.fixture
def pay(monkeypatch):
    fake = FakePay()
    monkeypatch.setattr(paypal, "charge_vault", fake)
    return fake


def test_allow_within_limits(pay):
    r = core.propose_purchase("Demo Store", ["Mug"], "18.00")
    assert r["status"] == "PAID" and r["decision"] == "ALLOW"
    assert pay.calls == ["18.00"]
    assert all(c["ok"] for c in r["checks"]) and len(r["checks"]) == 7


def test_over_cap_is_denied_and_never_reaches_paypal(pay):
    r = core.propose_purchase("Demo Store", ["Gift card"], "400.00")
    assert r["status"] == "DENIED" and r["decision"] == "DENY"
    assert "per-transaction cap" in r["decision_reason"]
    assert pay.calls == []


def test_merchant_not_on_allowlist_is_denied(pay):
    r = core.propose_purchase("Gift Card Hub", ["Gift card"], "10.00")
    assert r["status"] == "DENIED" and "allowlist" in r["decision_reason"]
    assert pay.calls == []


def test_merchant_match_ignores_case(pay):
    assert core.propose_purchase("demo store", ["Mug"], "5.00")["status"] == "PAID"


def test_currency_lock(pay):
    r = core.propose_purchase("Demo Store", ["Mug"], "10.00", "EUR")
    assert r["status"] == "DENIED" and "currency" in r["decision_reason"]


def test_escalation_then_human_approves(pay):
    r = core.propose_purchase("Demo Store", ["Headphones"], "40.00")
    assert r["status"] == "AWAITING_APPROVAL" and r["decision"] == "ESCALATE"
    assert pay.calls == []
    assert [p["id"] for p in core.list_purchases(status="AWAITING_APPROVAL")] == [r["id"]]
    done = core.approve(r["id"], approver="tester")
    assert done["status"] == "PAID" and done["decision"] == "ALLOW"
    assert pay.calls == ["40.00"]
    with pytest.raises(ValueError):
        core.approve(r["id"])  # cannot approve twice
    assert ledger.verify()["ok"] is True


def test_escalation_then_human_rejects(pay):
    r = core.propose_purchase("Demo Store", ["Headphones"], "40.00")
    done = core.reject(r["id"])
    assert done["status"] == "APPROVAL_DENIED" and pay.calls == []
    with pytest.raises(ValueError):
        core.reject(r["id"])


def test_cannot_approve_something_that_was_not_escalated(pay):
    r = core.propose_purchase("Demo Store", ["Mug"], "10.00")
    with pytest.raises(ValueError):
        core.approve(r["id"])
    with pytest.raises(LookupError):
        core.approve("pur_missing")


def test_split_purchases_hit_the_cumulative_budget(pay):
    policy.set_policy({"auto_approve_limit": "50.00"})
    assert core.propose_purchase("Demo Store", ["a"], "40.00")["status"] == "PAID"
    assert core.propose_purchase("Demo Store", ["b"], "40.00")["status"] == "PAID"
    third = core.propose_purchase("Demo Store", ["c"], "40.00")
    assert third["status"] == "DENIED" and "daily budget" in third["decision_reason"]
    assert pay.calls == ["40.00", "40.00"]


def test_velocity_circuit_breaker(pay):
    policy.set_policy({"velocity_max_purchases": 3, "daily_budget": "1000.00", "weekly_budget": "3000.00"})
    for _ in range(3):
        assert core.propose_purchase("Demo Store", ["x"], "1.00")["status"] == "PAID"
    fourth = core.propose_purchase("Demo Store", ["x"], "1.00")
    assert fourth["status"] == "DENIED" and "circuit breaker" in fourth["decision_reason"]


def test_denied_attempts_also_count_toward_velocity(pay):
    policy.set_policy({"velocity_max_purchases": 2})
    core.propose_purchase("Evil Shop", ["x"], "1.00")
    core.propose_purchase("Evil Shop", ["x"], "1.00")
    third = core.propose_purchase("Demo Store", ["x"], "1.00")
    assert third["status"] == "DENIED" and "circuit breaker" in third["decision_reason"]


def test_approval_rechecks_budgets(pay):
    r = core.propose_purchase("Demo Store", ["Headphones"], "40.00")
    assert r["status"] == "AWAITING_APPROVAL"
    policy.set_policy({"daily_budget": "10.00"})
    done = core.approve(r["id"])
    assert done["status"] == "DENIED" and "re-checked at approval" in done["decision_reason"]
    assert pay.calls == []


def test_policy_validation():
    bad = [
        {"per_txn_cap": "abc"},
        {"per_txn_cap": "-1"},
        {"auto_approve_limit": "80.00"},
        {"nope": 1},
        {"velocity_max_purchases": 0},
        {"velocity_max_purchases": True},
        {"merchant_allowlist": "Demo Store"},
        {"merchant_allowlist": [""]},
        {"currency": "DOLLARS"},
    ]
    for raw in bad:
        with pytest.raises(ValueError):
            policy.set_policy(raw)
    assert policy.get_policy() == policy.validate_policy({})  # nothing was saved


def test_policy_updates_merge_and_are_ledgered():
    new = policy.set_policy({"daily_budget": "20.5"})
    assert new["daily_budget"] == "20.50" and new["per_txn_cap"] == "50.00"
    assert policy.get_policy() == new
    assert ledger.verify() == {"ok": True, "entries": 1}


def test_budget_status(pay):
    core.propose_purchase("Demo Store", ["Mug"], "18.00")
    b = policy.budget_status()
    assert b["daily_remaining"] == "82.00" and b["weekly_remaining"] == "282.00"
    assert b["allowed_merchants"] == ["Demo Store"] and b["per_txn_cap"] == "50.00"


def test_old_database_is_migrated(tmp_path, monkeypatch):
    old = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(old)
    conn.execute(
        "CREATE TABLE purchases (id TEXT PRIMARY KEY, created_at REAL NOT NULL, merchant TEXT NOT NULL,"
        " items TEXT NOT NULL, amount TEXT NOT NULL, currency TEXT NOT NULL, rationale TEXT NOT NULL,"
        " decision TEXT NOT NULL, decision_reason TEXT NOT NULL, status TEXT NOT NULL,"
        " paypal_order_id TEXT, paypal_capture_id TEXT, approve_url TEXT, error TEXT)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DB_PATH", str(old))
    assert core.list_purchases() == []
    conn = sqlite3.connect(old)
    assert "checks" in {r[1] for r in conn.execute("PRAGMA table_info(purchases)")}
    conn.close()


def test_rest_policy_budget_and_approvals(pay):
    from fastapi.testclient import TestClient
    from server.main import app

    client = TestClient(app)
    assert client.get("/v1/policy").json()["per_txn_cap"] == "50.00"
    assert client.put("/v1/policy", json={"daily_budget": "200.00"}).json()["daily_budget"] == "200.00"
    assert client.put("/v1/policy", json={"per_txn_cap": "zzz"}).status_code == 422
    assert client.get("/v1/budget").json()["daily_remaining"] == "200.00"

    r = client.post("/v1/purchases", json={"merchant": "Demo Store", "items": ["Headphones"], "amount": "40.00"})
    assert r.json()["status"] == "AWAITING_APPROVAL"
    pid = r.json()["id"]
    assert [p["id"] for p in client.get("/v1/approvals").json()] == [pid]
    assert client.post("/v1/purchases/%s/approve" % pid).json()["status"] == "PAID"
    assert client.post("/v1/purchases/%s/approve" % pid).status_code == 409
    assert client.post("/v1/purchases/pur_missing/approve").status_code == 404

    r2 = client.post("/v1/purchases", json={"merchant": "Demo Store", "items": ["Cam"], "amount": "45.00"})
    assert client.post("/v1/purchases/%s/reject" % r2.json()["id"]).json()["status"] == "APPROVAL_DENIED"
    assert client.get("/v1/audit/verify").json()["ok"] is True


def test_mcp_budget_and_decisions(pay):
    from server import mcp_server

    tools = asyncio.run(mcp_server.mcp.list_tools())
    assert {"propose_purchase", "get_budget", "get_purchase_status"} <= {t.name for t in tools}
    assert mcp_server.get_budget()["per_txn_cap"] == "50.00"
    denied = mcp_server.propose_purchase("Gift Card Hub", ["Gift card"], "400.00")
    assert denied["status"] == "DENIED" and denied["decision"] == "DENY"
