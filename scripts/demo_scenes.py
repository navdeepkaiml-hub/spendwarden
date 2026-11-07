"""Run the demo scenes against the local SpendWarden server.

    python scripts/demo_scenes.py

A  normal purchase                     -> verified and paid
B  oversized gift-card attack           -> denied by the policy rules
C  pricier purchase                     -> a human approves, then paid
D  in-policy injection attack           -> the rules alone would only ask a human; the intent verifier denies it

Scenes A and C make real PayPal SANDBOX payments. The script first sets a demo policy (printed below) so it
can be re-run. Scene D and the verification in A and C need the intent verifier (Gemini key, or replay mode).
"""
import argparse
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server import demo  # noqa: E402  (scene definitions shared with the hosted demo)

from dotenv import load_dotenv
load_dotenv()
import os
ADMIN_HEADERS = {"X-Admin-Token": os.getenv("ADMIN_TOKEN")} if os.getenv("ADMIN_TOKEN") else {}

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8000")
parser.add_argument("--auto-approve", action="store_true", help="Do not pause before the human approval in scene C")
parser.add_argument("--leave-pending", action="store_true", help="Leave scene C waiting so you can approve it in the dashboard")
args = parser.parse_args()
URL = args.url


def call(method, path, **kw):
    try:
        resp = requests.request(method, URL + path, timeout=90, headers=ADMIN_HEADERS, **kw)
    except requests.ConnectionError:
        sys.exit("Could not reach %s. Is the server running? (python -m uvicorn server.main:app --reload)" % URL)
    if resp.status_code >= 400:
        sys.exit("HTTP %s from %s: %s" % (resp.status_code, path, resp.text))
    return resp.json()


def mandate(task):
    return call("POST", "/v1/mandates", json={"task": task})["id"]


def buy(mandate_id, merchant, item, amount, rationale, evidence):
    return call("POST", "/v1/purchases", json={
        "merchant": merchant, "items": [item], "amount": amount, "rationale": rationale,
        "mandate_id": mandate_id, "evidence": [evidence]})


def show(title, r):
    print("\n=== %s" % title)
    print("  %s  %s USD  at %s" % (", ".join(r["items"]), r["amount"], r["merchant"]))
    print("  decision: %s   status: %s" % (r["decision"], r["status"]))
    print("  reason:   %s" % r["decision_reason"])
    if r.get("paypal_capture_id"):
        print("  PayPal capture id: %s" % r["paypal_capture_id"])
    if r.get("error"):
        print("  error: %s" % r["error"])


print("Setting demo policy:")
print(json.dumps(call("PUT", "/v1/policy", json=demo.DEMO_POLICY)))

mandates = {}
for spec in demo.SCENES:
    if spec["mandate"] not in mandates:
        mandates[spec["mandate"]] = mandate(demo.TASKS[spec["mandate"]])
    r = buy(mandates[spec["mandate"]], spec["merchant"], spec["item"], spec["amount"], spec["rationale"], spec["evidence"])
    show(spec["title"], r)
    if spec["key"] == "C" and r["status"] == "AWAITING_APPROVAL":
        if args.leave_pending:
            print("\nScene C is waiting for a human. Open the dashboard and approve or reject it there.")
        else:
            if not args.auto_approve:
                input("\nA human reviews scene C. Press Enter to approve it... ")
            show("C. After human approval", call("POST", "/v1/purchases/%s/approve" % r["id"]))

print("\nBudget now:", json.dumps(call("GET", "/v1/budget")))
print("Audit ledger:", json.dumps(call("GET", "/v1/audit/verify")))
