"""Spending policy: rules, persistence and the decision engine.

Rules are deterministic. A failed 'deny' rule means DENY. A failed 'escalate' rule means a
human must approve. The LLM verifier (Day 4) can only make a decision stricter, never looser.
"""
import json
import time
from decimal import Decimal, InvalidOperation

from server import db, ledger

DEFAULT_POLICY = {
    "currency": "USD",
    "per_txn_cap": "50.00",
    "auto_approve_limit": "30.00",
    "daily_budget": "100.00",
    "weekly_budget": "300.00",
    "merchant_allowlist": ["Demo Store"],
    "velocity_max_purchases": 5,
    "velocity_window_seconds": 60,
    "require_mandate": False,
    "verifier_escalate_below": 0.7,
    "verifier_deny_below": 0.4,
}
MONEY_KEYS = ("per_txn_cap", "auto_approve_limit", "daily_budget", "weekly_budget")
DAY = 24 * 3600
WEEK = 7 * DAY
# Money that counts against budgets: already paid, or paid-pending-buyer-approval.
COUNTED_STATUSES = ("PAID", "AWAITING_PAYER")


def _money(value, name):
    try:
        d = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValueError("%s must be a number like 50.00" % name)
    if not d.is_finite() or d <= 0:
        raise ValueError("%s must be a number greater than zero" % name)
    return d


def validate_policy(raw, base=None):
    """Merge raw onto base (default: DEFAULT_POLICY), validate, and return a clean policy."""
    if not isinstance(raw, dict):
        raise ValueError("policy must be an object")
    unknown = set(raw) - set(DEFAULT_POLICY)
    if unknown:
        raise ValueError("unknown policy fields: %s" % sorted(unknown))
    merged = dict(base or DEFAULT_POLICY)
    merged.update(raw)

    out = {}
    currency = str(merged["currency"]).strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("currency must be a 3-letter code such as USD")
    out["currency"] = currency
    for key in MONEY_KEYS:
        out[key] = str(_money(merged[key], key))
    if Decimal(out["auto_approve_limit"]) > Decimal(out["per_txn_cap"]):
        raise ValueError("auto_approve_limit cannot exceed per_txn_cap")

    allow = merged["merchant_allowlist"]
    if not isinstance(allow, list) or not all(isinstance(m, str) and m.strip() for m in allow):
        raise ValueError("merchant_allowlist must be a list of merchant names")
    out["merchant_allowlist"] = [m.strip() for m in allow]

    for key in ("velocity_max_purchases", "velocity_window_seconds"):
        value = merged[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("%s must be a whole number of at least 1" % key)
        out[key] = value

    if not isinstance(merged["require_mandate"], bool):
        raise ValueError("require_mandate must be true or false")
    out["require_mandate"] = merged["require_mandate"]

    for key in ("verifier_escalate_below", "verifier_deny_below"):
        value = merged[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError("%s must be a number between 0 and 1" % key)
        out[key] = float(value)
    if out["verifier_deny_below"] > out["verifier_escalate_below"]:
        raise ValueError("verifier_deny_below cannot exceed verifier_escalate_below")
    return out


def get_policy():
    with db.connect() as conn:
        row = conn.execute("SELECT body FROM policy WHERE id = 1").fetchone()
    # Merge onto the defaults so policies saved by older versions gain any new fields.
    return validate_policy(json.loads(row["body"])) if row else validate_policy({})


def set_policy(raw, who="api"):
    before = get_policy()
    new = validate_policy(raw, base=before)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO policy (id, body) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET body = excluded.body",
            (json.dumps(new),),
        )
    ledger.append(None, "policy_updated", {"by": who, "before": before, "after": new})
    return new


def spent_since(seconds, exclude_id=None):
    marks = ",".join("?" for _ in COUNTED_STATUSES)
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT amount FROM purchases WHERE status IN (%s) AND created_at >= ? AND id != ?" % marks,
            (*COUNTED_STATUSES, time.time() - seconds, exclude_id or ""),
        ).fetchall()
    return sum((Decimal(r["amount"]) for r in rows), Decimal("0"))


def recent_proposals(seconds, exclude_id=None):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM purchases WHERE created_at >= ? AND id != ?",
            (time.time() - seconds, exclude_id or ""),
        ).fetchone()
    return row["n"]


def evaluate(proposal, pol, purchase_id, skip=(), has_mandate=False):
    """Run every rule. Returns {"decision", "reason", "checks"}."""
    amount = Decimal(proposal["amount"])
    checks = []

    def add(rule, ok, level, detail):
        if rule not in skip:
            checks.append({"rule": rule, "ok": bool(ok), "level": level, "detail": detail})

    add("currency_lock", proposal["currency"] == pol["currency"], "deny",
        "currency %s, policy allows only %s" % (proposal["currency"], pol["currency"]))

    if pol["require_mandate"]:
        add("mandate_required", has_mandate, "deny",
            "this policy requires every purchase to reference a mandate created by the user")

    allowed = {m.lower() for m in pol["merchant_allowlist"]}
    add("merchant_allowlist", proposal["merchant"].lower() in allowed, "deny",
        "merchant '%s' is not on the allowlist" % proposal["merchant"])

    cap = Decimal(pol["per_txn_cap"])
    add("per_txn_cap", amount <= cap, "deny",
        "amount %s exceeds the per-transaction cap %s" % (amount, cap))

    if "daily_budget" not in skip:
        day_total = spent_since(DAY, purchase_id) + amount
        limit = Decimal(pol["daily_budget"])
        add("daily_budget", day_total <= limit, "deny",
            "24-hour spend would be %s, over the daily budget %s" % (day_total, limit))

    if "weekly_budget" not in skip:
        week_total = spent_since(WEEK, purchase_id) + amount
        limit = Decimal(pol["weekly_budget"])
        add("weekly_budget", week_total <= limit, "deny",
            "7-day spend would be %s, over the weekly budget %s" % (week_total, limit))

    if "velocity" not in skip:
        window = pol["velocity_window_seconds"]
        recent = recent_proposals(window, purchase_id)
        add("velocity", recent < pol["velocity_max_purchases"], "deny",
            "%d requests in the last %ds, circuit breaker limit is %d" % (recent, window, pol["velocity_max_purchases"]))

    auto = Decimal(pol["auto_approve_limit"])
    add("auto_approve_limit", amount <= auto, "escalate",
        "amount %s is above the auto-approve limit %s, a human must approve" % (amount, auto))

    failed_deny = [c for c in checks if not c["ok"] and c["level"] == "deny"]
    failed_escalate = [c for c in checks if not c["ok"] and c["level"] == "escalate"]
    if failed_deny:
        return {"decision": "DENY", "reason": "; ".join(c["detail"] for c in failed_deny), "checks": checks}
    if failed_escalate:
        return {"decision": "ESCALATE", "reason": "; ".join(c["detail"] for c in failed_escalate), "checks": checks}
    return {"decision": "ALLOW", "reason": "all policy checks passed", "checks": checks}


def budget_status():
    pol = get_policy()
    zero = Decimal("0")
    day_left = max(Decimal(pol["daily_budget"]) - spent_since(DAY), zero)
    week_left = max(Decimal(pol["weekly_budget"]) - spent_since(WEEK), zero)
    return {
        "currency": pol["currency"],
        "per_txn_cap": pol["per_txn_cap"],
        "auto_approve_limit": pol["auto_approve_limit"],
        "daily_remaining": str(day_left),
        "weekly_remaining": str(week_left),
        "allowed_merchants": pol["merchant_allowlist"],
    }


_SEVERITY = {"ALLOW": 0, "ESCALATE": 1, "DENY": 2}


def tighten(verdict, check):
    """Add one more check to a verdict. A failed check can only make the decision stricter."""
    checks = verdict["checks"] + [check]
    decision, reason = verdict["decision"], verdict["reason"]
    if check.get("ok", True):
        if decision == "ALLOW" and check.get("note"):
            reason = "%s; %s" % (reason, check["note"])
    else:
        new = "DENY" if check["level"] == "deny" else "ESCALATE"
        if decision == "ALLOW":
            reason = check["detail"]  # replace "all policy checks passed"
        elif decision == "ESCALATE" and new == "DENY":
            reason = "%s (the policy rules alone would have asked a human: %s)" % (check["detail"], reason)
        else:
            reason = "%s; %s" % (reason, check["detail"])
        if _SEVERITY[new] > _SEVERITY[decision]:
            decision = new
    return {"decision": decision, "reason": reason, "checks": checks}
