"""Send a purchase proposal to the local SpendWarden server.

Usage (server must be running):
    python scripts/try_purchase.py
    python scripts/try_purchase.py --amount 25.00 --merchant "Demo Store" --item "Mug"
"""
import argparse
import json
import sys

import requests

from dotenv import load_dotenv
load_dotenv()
import os
ADMIN_HEADERS = {"X-Admin-Token": os.getenv("ADMIN_TOKEN")} if os.getenv("ADMIN_TOKEN") else {}

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8000")
parser.add_argument("--merchant", default="Demo Store")
parser.add_argument("--item", action="append", default=None)
parser.add_argument("--amount", default="18.00")
parser.add_argument("--currency", default="USD")
parser.add_argument("--rationale", default="Birthday gift the user asked for")
args = parser.parse_args()

body = {
    "merchant": args.merchant,
    "items": args.item or ["Ceramic mug"],
    "amount": args.amount,
    "currency": args.currency,
    "rationale": args.rationale,
}

try:
    resp = requests.post(args.url + "/v1/purchases", json=body, headers=ADMIN_HEADERS, timeout=60)
except requests.ConnectionError:
    sys.exit("Could not reach %s. Is the server running? (python -m uvicorn server.main:app --reload)" % args.url)

print("HTTP %s" % resp.status_code)
print(json.dumps(resp.json(), indent=2))

data = resp.json()
if data.get("status") == "AWAITING_PAYER":
    print("\nApproval mode: open this link as the sandbox buyer, approve, then run:")
    print("  curl.exe -X POST %s/v1/purchases/%s/capture" % (args.url, data["id"]))
    print("  " + data["approve_url"])
elif data.get("status") == "PAID":
    print("\nPAID. PayPal capture id: %s" % data["paypal_capture_id"])
