"""Day 2 spike: can SpendWarden pay with NO redirect? (PayPal vault, merchant-initiated)

First run (buyer approves once, then a payment is charged with no redirect):
    python scripts/paypal_vault_spike.py

Later runs (no human at all - this is what an agent purchase will look like):
    python scripts/paypal_vault_spike.py --reuse --amount 25.00

Needs PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env. Sandbox only.
The saved token id is kept in a local file named .vault_token (do not commit it).
"""
import argparse
import os
import sys
import uuid
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

BASE = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com").rstrip("/")
CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
TOKEN_FILE = Path(".vault_token")
TIMEOUT = 30

VAULT_HINT = """
Hints if saving the buyer failed:
  1. developer.paypal.com > Apps & Credentials > (Sandbox) your app > under
     'App Feature Options' tick 'Accept payments', expand 'Advanced options' and
     make sure 'Vault' is selected. Save.
  2. Saving PayPal wallets is documented for specific countries (US is listed, India
     is not). Use a US Business sandbox account for the app and a US Personal sandbox
     account as the buyer.
  3. Send me the 'name' and 'detail' lines above.
"""


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
        fail("Could not get an access token. Check the Client ID and Secret in .env.")
    return resp.json()["access_token"]


def headers(token):
    return {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "PayPal-Request-Id": uuid.uuid4().hex,
    }


def create_setup_token(token):
    body = {
        "payment_source": {
            "paypal": {
                "description": "SpendWarden demo agent spending mandate",
                "usage_type": "MERCHANT",
                "usage_pattern": "IMMEDIATE",
                "customer_type": "CONSUMER",
                "permit_multiple_payment_tokens": False,
                "experience_context": {
                    "shipping_preference": "NO_SHIPPING",
                    "payment_method_preference": "IMMEDIATE_PAYMENT_REQUIRED",
                    "brand_name": "SpendWarden demo",
                    "locale": "en-US",
                    "return_url": "https://example.com/return",
                    "cancel_url": "https://example.com/cancel",
                },
            }
        }
    }
    resp = requests.post(BASE + "/v3/vault/setup-tokens", headers=headers(token), json=body, timeout=TIMEOUT)
    if resp.status_code not in (200, 201):
        show_error(resp)
        print(VAULT_HINT)
        fail("Creating the setup token failed.")
    data = resp.json()
    link = None
    for item in data.get("links", []):
        if item.get("rel") == "approve":
            link = item["href"]
    if not link:
        fail("No approve link in the setup token response: %s" % data.get("links"))
    return data["id"], link


def create_payment_token(token, setup_id):
    body = {"payment_source": {"token": {"id": setup_id, "type": "SETUP_TOKEN"}}}
    resp = requests.post(BASE + "/v3/vault/payment-tokens", headers=headers(token), json=body, timeout=TIMEOUT)
    if resp.status_code not in (200, 201):
        show_error(resp)
        print("If the error says the setup token is not approved, approve it as the buyer and try again.")
        print(VAULT_HINT)
        fail("Creating the payment token failed.")
    return resp.json()["id"]


def charge(token, vault_id, amount, currency):
    body = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": "spendwarden-vault-spike",
                "description": "SpendWarden agent purchase (sandbox)",
                "amount": {"currency_code": currency, "value": amount},
            }
        ],
        "payment_source": {"paypal": {"vault_id": vault_id}},
    }
    resp = requests.post(BASE + "/v2/checkout/orders", headers=headers(token), json=body, timeout=TIMEOUT)
    if resp.status_code not in (200, 201):
        show_error(resp)
        fail("Charging the saved payment method failed.")
    return resp.json()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--amount", default="18.00")
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--reuse", action="store_true", help="Use the saved .vault_token, no buyer step")
    args = parser.parse_args()

    if not CLIENT_ID or not CLIENT_SECRET:
        fail("PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET missing. Fill them in .env.")

    print("1) Getting access token...")
    token = get_token()
    print("   ok")

    if args.reuse:
        if not TOKEN_FILE.exists():
            fail("No .vault_token file yet. Run once without --reuse first.")
        vault_id = TOKEN_FILE.read_text().strip()
        print("2) Using saved payment token: %s" % vault_id)
    else:
        print("2) Creating a setup token (the buyer consents once)...")
        setup_id, link = create_setup_token(token)
        print("   setup token: %s" % setup_id)
        print("3) Approve as the sandbox BUYER (Personal account) at:")
        print("   " + link)
        input("   Press Enter once you have approved... ")
        print("4) Turning the approved setup token into a payment token...")
        vault_id = create_payment_token(token, setup_id)
        TOKEN_FILE.write_text(vault_id)
        print("   payment token: %s (saved to .vault_token)" % vault_id)

    print("Charging %s %s with NO redirect..." % (args.amount, args.currency))
    result = charge(token, vault_id, args.amount, args.currency)
    print("   order id:     %s" % result.get("id"))
    print("   order status: %s" % result.get("status"))
    try:
        cap = result["purchase_units"][0]["payments"]["captures"][0]
        print("   capture id:   %s" % cap["id"])
        print("   capture:      %s %s %s" % (cap["status"], cap["amount"]["value"], cap["amount"]["currency_code"]))
    except (KeyError, IndexError):
        print("   no capture in the response. Links: %s" % result.get("links"))

    if result.get("status") == "COMPLETED":
        print("\nDONE: paid with no redirect. SpendWarden can execute approved purchases on its own.")
    else:
        print("\nStatus is not COMPLETED. Send me the lines above.")


if __name__ == "__main__":
    main()
