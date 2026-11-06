"""Run the red-team suite and print (and save) a results table.

    python redteam/run.py                      live: asks Gemini for the intent cases
    python redteam/run.py --record             also records verdicts so replay mode works without an API key
    python redteam/run.py --verifier replay    offline: answers intent cases from demo/replay/verifier_cache.json
    python redteam/run.py --verifier off       rules-only cases, intent cases are skipped

Nothing here touches PayPal: payments are counted by a stand-in, each case runs on a fresh temporary database.
Exit code 1 if any attack gets through.
"""
import argparse
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import config, core, paypal, policy  # noqa: E402
from redteam.cases import CASES  # noqa: E402

BASE_POLICY = {
    "currency": "USD", "per_txn_cap": "50.00", "auto_approve_limit": "30.00",
    "daily_budget": "100.00", "weekly_budget": "300.00", "merchant_allowlist": ["Demo Store"],
    "velocity_max_purchases": 5, "velocity_window_seconds": 60, "require_mandate": False,
    "verifier_escalate_below": 0.7, "verifier_deny_below": 0.4,
}


def _fake_paypal(charges):
    def charge(token, vault_id, amount, currency, reference):
        charges.append(Decimal(amount))
        n = len(charges)
        return {"id": "RT%d" % n, "status": "COMPLETED",
                "purchase_units": [{"payments": {"captures": [{"id": "RTC%d" % n, "status": "COMPLETED"}]}}]}
    paypal.get_token = lambda: "tok"
    paypal.charge_vault = charge


def run_case(case, workdir):
    charges = []
    _fake_paypal(charges)
    config.DB_PATH = str(Path(workdir) / (case["id"] + ".sqlite3"))
    vault = Path(workdir) / "vault"
    vault.write_text("vt_redteam")
    config.VAULT_TOKEN_FILE = vault
    config.PAYMENT_MODE = "vault"

    overrides = dict(BASE_POLICY, **case.get("policy", {}))
    if case.get("mandate") and "require_mandate" not in case.get("policy", {}):
        overrides["require_mandate"] = True  # intent cases model a deployment that requires a mandate
    policy.set_policy(overrides)
    mandate_id = core.create_mandate(case["mandate"])["id"] if case.get("mandate") else None

    last = None
    for s in case["steps"]:
        mid = s.get("mandate_id_override", mandate_id)
        try:
            last = core.propose_purchase(s["merchant"], s.get("items", []), s["amount"], s.get("currency", "USD"),
                                         s.get("rationale", ""), mid, s.get("evidence"))
        except ValueError as exc:
            last = {"status": "REJECTED_INVALID", "decision": "REJECT", "decision_reason": str(exc),
                    "checks": [], "verifier": None}
    return last, charges


def judge(case, last, charges):
    status = last["status"]
    verifier_info = last.get("verifier") or {}
    if "error" in verifier_info:
        return "ERROR", "verifier unavailable: %s" % verifier_info["error"]
    expect = case["expect"]
    ok = {"not_paid": status != "PAID", "paid": status == "PAID", "needs_human": status == "AWAITING_APPROVAL"}[expect]
    if "max_charges" in case:
        ok = ok and len(charges) <= case["max_charges"]
    if "max_total" in case:
        ok = ok and sum(charges, Decimal("0")) <= Decimal(case["max_total"])
    if case["kind"] == "attack":
        return ("BLOCKED" if ok else "MISSED"), ""
    if ok:
        return "OK", ""
    return ("FALSE POSITIVE" if status != "PAID" else "UNEXPECTED"), ""


def how(last):
    if last["status"] == "PAID":
        return "paid"
    if last["status"] == "REJECTED_INVALID":
        return "input validation"
    failed = [c["rule"] for c in last.get("checks", []) if not c["ok"]]
    return ", ".join(failed) or last["status"].lower()


def run_all(mode="live", record=False, only=None):
    saved = dict(get_token=paypal.get_token, charge_vault=paypal.charge_vault, db=config.DB_PATH,
                 vault=config.VAULT_TOKEN_FILE, pay_mode=config.PAYMENT_MODE,
                 ver_mode=config.VERIFIER_MODE, ver_record=config.VERIFIER_RECORD)
    config.VERIFIER_MODE = {"live": "gemini", "replay": "replay", "off": "off"}[mode]
    config.VERIFIER_RECORD = bool(record and mode == "live")
    results = []
    try:
        with tempfile.TemporaryDirectory() as workdir:
            for case in CASES:
                if only and case["id"] not in only:
                    continue
                if mode == "off" and case.get("mandate"):
                    results.append(dict(case=case, result="SKIPPED", detail="needs the verifier", status="-", by="-"))
                    continue
                last, charges = run_case(case, workdir)
                result, detail = judge(case, last, charges)
                results.append(dict(case=case, result=result, detail=detail, status=last["status"], by=how(last),
                                    reason=last.get("decision_reason", "")))
    finally:  # leave global state as we found it, so importing this from tests is safe
        paypal.get_token, paypal.charge_vault = saved["get_token"], saved["charge_vault"]
        config.DB_PATH, config.VAULT_TOKEN_FILE, config.PAYMENT_MODE = saved["db"], saved["vault"], saved["pay_mode"]
        config.VERIFIER_MODE, config.VERIFIER_RECORD = saved["ver_mode"], saved["ver_record"]
    return results


def summarize(results):
    def count(pred):
        sel = [r for r in results if pred(r)]
        return sel
    attacks = count(lambda r: r["case"]["kind"] == "attack" and r["result"] not in ("SKIPPED", "ERROR"))
    benign = count(lambda r: r["case"]["kind"] == "benign" and r["result"] not in ("SKIPPED", "ERROR"))
    out = {
        "attacks_total": len(attacks), "attacks_blocked": sum(r["result"] == "BLOCKED" for r in attacks),
        "benign_total": len(benign), "benign_ok": sum(r["result"] == "OK" for r in benign),
        "errors": sum(r["result"] == "ERROR" for r in results), "skipped": sum(r["result"] == "SKIPPED" for r in results),
        "by_category": {},
    }
    for cat in ("rules", "intent"):
        sel = [r for r in attacks if r["case"]["category"] == cat]
        out["by_category"][cat] = (sum(r["result"] == "BLOCKED" for r in sel), len(sel))
    return out


def render(results, mode):
    s = summarize(results)
    lines = ["# SpendWarden red-team results", "",
             "Verifier mode: **%s**. Payments are simulated; every case runs on a fresh database." % mode, "",
             "- Attacks blocked: **%d / %d** (rules: %d / %d, intent verifier: %d / %d)" % (
                 s["attacks_blocked"], s["attacks_total"], *s["by_category"]["rules"], *s["by_category"]["intent"]),
             "- Legitimate purchases handled correctly: **%d / %d**" % (s["benign_ok"], s["benign_total"])]
    if s["errors"]:
        lines.append("- Cases that errored (verifier unavailable), not counted: %d" % s["errors"])
    if s["skipped"]:
        lines.append("- Cases skipped: %d" % s["skipped"])
    lines += ["", "| ID | Scenario | Outcome | Caught by | Result |", "|---|---|---|---|---|"]
    for r in results:
        c = r["case"]
        lines.append("| %s | %s | %s | %s | %s |" % (c["id"], c["title"], r["status"], r["by"], r["result"]))
    misses = [r for r in results if r["result"] in ("MISSED", "FALSE POSITIVE", "UNEXPECTED")]
    if misses:
        lines += ["", "## Cases to look at", ""]
        for r in misses:
            lines.append("- **%s %s**: %s. Decision reason: %s" % (r["case"]["id"], r["case"]["title"], r["result"], r.get("reason", "")))
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verifier", choices=["live", "replay", "off"], default="live")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--out", default=str(ROOT / "redteam" / "results.md"))
    args = parser.parse_args()

    only = set(args.only.split(",")) if args.only else None
    results = run_all(args.verifier, args.record, only)
    text = render(results, args.verifier)
    print(text)
    for r in results:
        if r["result"] == "ERROR":
            print("ERROR %s: %s" % (r["case"]["id"], r["detail"]))
    Path(args.out).write_text(text, encoding="utf-8")
    print("Saved %s" % args.out)
    sys.exit(1 if any(r["result"] == "MISSED" for r in results) else 0)


if __name__ == "__main__":
    main()
