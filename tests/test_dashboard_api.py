"""Dashboard endpoints, access control and the static files."""
import pytest
from fastapi.testclient import TestClient

from server import config, core, ledger, llm, paypal, policy
from server.main import app


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.sqlite3"))
    vault = tmp_path / ".vault_token"
    vault.write_text("vt_test_123")
    monkeypatch.setattr(config, "VAULT_TOKEN_FILE", vault)
    monkeypatch.setattr(config, "PAYMENT_MODE", "vault")
    monkeypatch.setattr(config, "VERIFIER_MODE", "gemini")
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")
    monkeypatch.setattr(config, "AGENT_TOKEN", "")
    monkeypatch.setattr(paypal, "get_token", lambda: "tok")
    monkeypatch.setattr(paypal, "charge_vault", lambda *a, **k: {
        "id": "O1", "status": "COMPLETED",
        "purchase_units": [{"payments": {"captures": [{"id": "C1", "status": "COMPLETED"}]}}]})
    monkeypatch.setattr(llm, "generate_json", lambda s, p: {
        "match_score": 0.95, "injection_detected": False, "flagged_text": "", "reasons": ["ok"]})


def test_receipt_contains_purchase_mandate_and_chain():
    client = TestClient(app)
    m = core.create_mandate("Buy a ceramic mug under $20")
    r = client.post("/v1/purchases", json={"merchant": "Demo Store", "items": ["Mug"], "amount": "18.00",
                                           "mandate_id": m["id"], "evidence": ["page"]}).json()
    rec = client.get("/v1/purchases/%s/receipt" % r["id"]).json()
    assert rec["purchase"]["status"] == "PAID" and rec["mandate"]["task"] == "Buy a ceramic mug under $20"
    assert [e["event"] for e in rec["entries"]] == ["proposed", "decided", "payment_result"]
    assert rec["chain"]["ok"] is True
    assert client.get("/v1/purchases/pur_nope/receipt").status_code == 404


def test_audit_listing_is_newest_first_and_limited():
    client = TestClient(app)
    for _ in range(3):
        client.post("/v1/purchases", json={"merchant": "Demo Store", "items": ["Mug"], "amount": "5.00"})
    rows = client.get("/v1/audit?limit=4").json()
    assert len(rows) == 4 and rows[0]["seq"] > rows[1]["seq"]
    assert {"seq", "ts", "event", "purchase_id", "hash", "prev_hash"} <= set(rows[0])


def test_dashboard_is_served():
    client = TestClient(app)
    root = client.get("/", follow_redirects=False)
    assert root.status_code in (302, 307) and root.headers["location"] == "/ui/"
    page = client.get("/ui/")
    assert page.status_code == 200 and "SpendWarden" in page.text
    assert client.get("/ui/app.js").status_code == 200 and client.get("/ui/style.css").status_code == 200


def test_everything_is_open_when_no_tokens_are_configured():
    client = TestClient(app)
    assert client.get("/v1/policy").status_code == 200
    assert client.get("/health").json()["auth_required"] is False


def test_admin_routes_need_the_admin_token(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "admin-secret")
    client = TestClient(app)
    for method, path in [("get", "/v1/policy"), ("get", "/v1/purchases"), ("get", "/v1/approvals"),
                         ("get", "/v1/audit"), ("get", "/v1/audit/verify"), ("get", "/v1/mandates"),
                         ("post", "/v1/purchases/pur_x/approve"), ("post", "/v1/purchases/pur_x/reject")]:
        assert getattr(client, method)(path).status_code == 401, path
    assert client.put("/v1/policy", json={"daily_budget": "5"}).status_code == 401
    assert client.post("/v1/mandates", json={"task": "Buy a mug please"}).status_code == 401
    assert client.get("/v1/policy", headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.get("/v1/policy", headers={"X-Admin-Token": "admin-secret"}).status_code == 200
    assert client.get("/health").json()["auth_required"] is True  # health stays public and reveals no secrets


def test_an_agent_token_cannot_approve_its_own_purchase(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "AGENT_TOKEN", "agent-secret")
    client = TestClient(app)
    agent = {"X-Agent-Token": "agent-secret"}
    admin = {"X-Admin-Token": "admin-secret"}

    assert client.post("/v1/purchases", json={"merchant": "Demo Store", "amount": "5"}).status_code == 401
    assert client.get("/v1/budget").status_code == 401
    r = client.post("/v1/purchases", json={"merchant": "Demo Store", "items": ["x"], "amount": "40.00"}, headers=agent)
    assert r.status_code == 200 and r.json()["status"] == "AWAITING_APPROVAL"
    pid = r.json()["id"]
    assert client.get("/v1/budget", headers=agent).status_code == 200
    assert client.get("/v1/purchases/%s" % pid, headers=agent).status_code == 200

    assert client.post("/v1/purchases/%s/approve" % pid, headers=agent).status_code == 401  # the point of the split
    assert client.put("/v1/policy", json={"per_txn_cap": "9999"}, headers=agent).status_code == 401
    assert client.get("/v1/purchases", headers=agent).status_code == 401
    assert client.post("/v1/purchases/%s/approve" % pid, headers=admin).json()["status"] == "PAID"
    # the admin token also works on agent routes (the dashboard and scripts use one header)
    assert client.get("/v1/budget", headers=admin).status_code == 200


def test_agent_routes_stay_open_if_only_the_admin_token_is_set(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "admin-secret")
    client = TestClient(app)
    assert client.get("/v1/budget").status_code == 200
