"""Core logic shared by the REST API and the MCP server.

Flow: validate -> record -> policy decision -> (ALLOW) pay | (ESCALATE) wait for a human | (DENY) stop.
Every step is written to the hash-chained ledger. The intent verifier (Day 4) plugs in at the
decision step and can only make the outcome stricter.
"""
import json
import threading
import time
import uuid
from decimal import Decimal, InvalidOperation

from server import config, db, ledger, paypal, policy

# One purchase is decided and paid at a time, so budget checks cannot race each other.
_lock = threading.RLock()

ALLOWED_UPDATE_COLUMNS = {
    "decision", "decision_reason", "status", "paypal_order_id",
    "paypal_capture_id", "approve_url", "error", "checks",
}


class PaymentConfigError(Exception):
    pass


def _clean(merchant, items, amount, currency, rationale):
    merchant = (merchant or "").strip()
    if not merchant:
        raise ValueError("merchant is required")
    try:
        amt = Decimal(str(amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValueError("amount must be a number like 18.00")
    if not amt.is_finite() or amt <= 0:
        raise ValueError("amount must be a number greater than zero")
    currency = (currency or "USD").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("currency must be a 3-letter code such as USD")
    return {
        "merchant": merchant,
        "items": [str(i) for i in (items or [])],
        "amount": str(amt),
        "currency": currency,
        "rationale": str(rationale or ""),
    }


def _update(purchase_id, **fields):
    bad = set(fields) - ALLOWED_UPDATE_COLUMNS
    if bad:
        raise ValueError("cannot update columns: %s" % sorted(bad))
    if not fields:
        return
    if isinstance(fields.get("checks"), (list, dict)):
        fields["checks"] = json.dumps(fields["checks"])
    sets = ", ".join("%s = ?" % col for col in fields)
    with db.connect() as conn:
        conn.execute("UPDATE purchases SET %s WHERE id = ?" % sets, list(fields.values()) + [purchase_id])


def _row_to_dict(row):
    d = dict(row)
    d["items"] = json.loads(d["items"])
    d["checks"] = json.loads(d["checks"]) if d.get("checks") else []
    return d


def get_purchase(purchase_id):
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM purchases WHERE id = ?", (purchase_id,)).fetchone()
    if row is None:
        raise LookupError("no purchase with id %s" % purchase_id)
    return _row_to_dict(row)


def list_purchases(limit=50, status=None):
    query = "SELECT * FROM purchases"
    args = []
    if status:
        query += " WHERE status = ?"
        args.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    args.append(int(limit))
    with db.connect() as conn:
        rows = conn.execute(query, args).fetchall()
    return [_row_to_dict(r) for r in rows]


def _read_vault_id():
    try:
        vault_id = config.VAULT_TOKEN_FILE.read_text().strip()
    except OSError:
        raise PaymentConfigError("No saved payment token (%s). Run scripts/paypal_vault_spike.py first." % config.VAULT_TOKEN_FILE)
    if not vault_id:
        raise PaymentConfigError("The saved payment token file is empty.")
    return vault_id


def _pay(purchase_id, p):
    try:
        token = paypal.get_token()
        if config.PAYMENT_MODE == "vault":
            result = paypal.summarize(
                paypal.charge_vault(token, _read_vault_id(), p["amount"], p["currency"], purchase_id)
            )
            if result["capture_status"] == "COMPLETED":
                fields = {"status": "PAID", "paypal_order_id": result["order_id"], "paypal_capture_id": result["capture_id"]}
            else:
                fields = {
                    "status": "PAYMENT_FAILED",
                    "paypal_order_id": result["order_id"],
                    "error": "unexpected order status %s" % result["order_status"],
                }
        elif config.PAYMENT_MODE == "approval":
            order_id, url = paypal.create_order_for_approval(token, p["amount"], p["currency"], purchase_id)
            fields = {"status": "AWAITING_PAYER", "paypal_order_id": order_id, "approve_url": url}
        else:
            raise PaymentConfigError("PAYMENT_MODE must be 'vault' or 'approval', got %r" % config.PAYMENT_MODE)
    except (paypal.PayPalError, PaymentConfigError) as exc:
        fields = {"status": "PAYMENT_FAILED", "error": str(exc)}
    _update(purchase_id, **fields)
    ledger.append(purchase_id, "payment_result", fields)
    return get_purchase(purchase_id)


def propose_purchase(merchant, items, amount, currency="USD", rationale=""):
    p = _clean(merchant, items, amount, currency, rationale)
    purchase_id = "pur_" + uuid.uuid4().hex[:12]
    with _lock:
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO purchases (id, created_at, merchant, items, amount, currency, rationale,"
                " decision, decision_reason, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (purchase_id, time.time(), p["merchant"], json.dumps(p["items"]), p["amount"],
                 p["currency"], p["rationale"], "PENDING", "", "RECEIVED"),
            )
        ledger.append(purchase_id, "proposed", p)

        verdict = policy.evaluate(p, policy.get_policy(), purchase_id)
        _update(purchase_id, decision=verdict["decision"], decision_reason=verdict["reason"], checks=verdict["checks"])
        ledger.append(purchase_id, "decided", verdict)

        if verdict["decision"] == "ALLOW":
            return _pay(purchase_id, p)
        status = "DENIED" if verdict["decision"] == "DENY" else "AWAITING_APPROVAL"
        _update(purchase_id, status=status)
        return get_purchase(purchase_id)


def approve(purchase_id, approver="dashboard"):
    """A human approves an escalated purchase. Hard rules are re-checked first, because
    budgets may have changed since the request was made."""
    with _lock:
        purchase = get_purchase(purchase_id)
        if purchase["status"] != "AWAITING_APPROVAL":
            raise ValueError("purchase is %s, not AWAITING_APPROVAL" % purchase["status"])
        p = {k: purchase[k] for k in ("merchant", "items", "amount", "currency", "rationale")}
        verdict = policy.evaluate(
            p, policy.get_policy(), purchase_id, skip=("velocity", "auto_approve_limit")
        )
        ledger.append(purchase_id, "human_approved", {"by": approver})
        if verdict["decision"] == "DENY":
            reason = "re-checked at approval time: " + verdict["reason"]
            _update(purchase_id, status="DENIED", decision="DENY", decision_reason=reason, checks=verdict["checks"])
            ledger.append(purchase_id, "denied_at_approval", {"reason": reason})
            return get_purchase(purchase_id)
        _update(purchase_id, decision="ALLOW", decision_reason="approved by a human (%s)" % approver)
        return _pay(purchase_id, p)


def reject(purchase_id, approver="dashboard"):
    with _lock:
        purchase = get_purchase(purchase_id)
        if purchase["status"] != "AWAITING_APPROVAL":
            raise ValueError("purchase is %s, not AWAITING_APPROVAL" % purchase["status"])
        _update(purchase_id, status="APPROVAL_DENIED", decision="DENY", decision_reason="rejected by a human (%s)" % approver)
        ledger.append(purchase_id, "human_rejected", {"by": approver})
        return get_purchase(purchase_id)


def capture_approved(purchase_id):
    """Approval-link mode only: capture an order after the buyer approved it on PayPal."""
    with _lock:
        purchase = get_purchase(purchase_id)
        if purchase["status"] != "AWAITING_PAYER":
            raise ValueError("purchase is %s, not AWAITING_PAYER" % purchase["status"])
        try:
            token = paypal.get_token()
            result = paypal.summarize(paypal.capture(token, purchase["paypal_order_id"]))
            if result["capture_status"] == "COMPLETED":
                fields = {"status": "PAID", "paypal_capture_id": result["capture_id"]}
            else:
                fields = {"status": "PAYMENT_FAILED", "error": "unexpected capture status %s" % result["capture_status"]}
        except paypal.PayPalError as exc:
            fields = {"status": "AWAITING_PAYER", "error": str(exc)}  # buyer may not have approved yet
        _update(purchase_id, **fields)
        ledger.append(purchase_id, "capture_result", fields)
        return get_purchase(purchase_id)
