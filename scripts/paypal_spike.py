"""Day 1 spike: create a PayPal sandbox order, approve it as the buyer, capture it.

Usage:
    python scripts/paypal_spike.py
    python scripts/paypal_spike.py --amount 25.00
    python scripts/paypal_spike.py --try-capture-first   # shows what happens without approval

Needs PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env (sandbox app credentials).
Sandbox only. No real money moves.
"""
import argparse
import os
import sys
import uuid

import requests
from dotenv import load_dotenv

load_dotenv()

BASE = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com").rstrip("/")
CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
TIMEOUT = 30


def fail(msg):
    print("ERROR: " + msg)
    sys.exit(1)


def show_error(resp):
    print("HTTP %s" % resp.status_code)
    print("PayPal-Debug-Id: %s" % resp.headers.get("PayPal-Debug-Id", "n/a"))
    try:
        body = resp.json()
        print("name:    %s" % body.get("name"))
        print("message: %s" % body.get("message"))
        for d in body.get("details", []):
            print("detail:  %s - %s" % (d.get("issue"), d.get("description")))
    except ValueError:
        print(resp.text[:500])


def get_token():
    resp = requests.post(
        BASE + "/v1/oauth2/token",
        auth=(CLIENT_ID, CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        show_error(resp)
        fail("Could not get an access token. Check the sandbox Client ID and Secret in .env.")
    return resp.json()["access_token"]


def headers(token):
    return {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "PayPal-Request-Id": str(uuid.uuid4()),
    }


def create_order(token, amount, currency):
    body = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": "spendwarden-spike",
                "description": "SpendWarden sandbox spike",
                "amount": {"currency_code": currency, "value": amount},
            }
        ],
        "payment_source": {
            "paypal": {
                "experience_context": {
                    "brand_name": "SpendWarden demo",
                    "user_action": "PAY_NOW",
                    "return_url": "https://example.com/return",
                    "cancel_url": "https://example.com/cancel",
                }
            }
        },
    }
    resp = requests.post(BASE + "/v2/checkout/orders", headers=headers(token), json=body, timeout=TIMEOUT)
    if resp.status_code not in (200, 201):
        show_error(resp)
        fail("Create order failed.")
    return resp.json()


def approval_link(order):
    for link in order.get("links", []):
        if link.get("rel") in ("payer-action", "approve"):
            return link["href"]
    return None


def get_order(token, order_id):
    resp = requests.get(BASE + "/v2/checkout/orders/" + order_id, headers=headers(token), timeout=TIMEOUT)
    if resp.status_code != 200:
        show_error(resp)
        return None
    return resp.json()


def capture(token, order_id):
    return requests.post(
        BASE + "/v2/checkout/orders/%s/capture" % order_id,
        headers=headers(token),
        json={},
        timeout=TIMEOUT,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--amount", default="18.00")
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--try-capture-first", action="store_true",
                        help="Attempt a capture before approval to see the error")
    args = parser.parse_args()

    if not CLIENT_ID or not CLIENT_SECRET:
        fail("PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET missing. Copy .env.example to .env and fill them in.")

    print("1) Getting access token...")
    token = get_token()
    print("   ok")

    print("2) Creating order for %s %s..." % (args.amount, args.currency))
    order = create_order(token, args.amount, args.currency)
    order_id = order["id"]
    print("   order id: %s   status: %s" % (order_id, order.get("status")))

    if args.try_capture_first:
        print("   (test) capturing BEFORE approval...")
        resp = capture(token, order_id)
        if resp.status_code in (200, 201):
            print("   unexpected: capture succeeded without approval")
        else:
            show_error(resp)

    link = approval_link(order)
    if not link:
        fail("No approval link in the response. Links were: %s" % order.get("links"))

    print("3) Approve the payment as the sandbox BUYER (Personal account):")
    print("   " + link)
    print("   Open it in a browser, log in with the sandbox Personal account, and approve.")
    input("   Press Enter here once you have approved... ")

    current = get_order(token, order_id)
    if current:
        print("   order status now: %s" % current.get("status"))

    print("4) Capturing payment...")
    resp = capture(token, order_id)
    if resp.status_code not in (200, 201):
        show_error(resp)
        fail("Capture failed. If the error is ORDER_NOT_APPROVED, approve the order first.")

    result = resp.json()
    unit = result["purchase_units"][0]
    cap = unit["payments"]["captures"][0]
    print("   order status:   %s" % result.get("status"))
    print("   capture id:     %s" % cap["id"])
    print("   capture status: %s" % cap["status"])
    print("   amount:         %s %s" % (cap["amount"]["value"], cap["amount"]["currency_code"]))
    print("")
    print("DONE: one sandbox payment captured. Record the result in docs/payment-path-notes.md")


if __name__ == "__main__":
    main()
