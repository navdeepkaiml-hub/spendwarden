"""Thin PayPal sandbox client (Orders v2 + vaulted payment tokens)."""
import uuid

import requests

from server import config

TIMEOUT = 30


class PayPalError(Exception):
    def __init__(self, message, debug_id=None, name=None):
        super().__init__(message)
        self.debug_id = debug_id
        self.name = name


def _raise(resp, what):
    name = None
    detail = ""
    try:
        body = resp.json()
        name = body.get("name")
        parts = ["%s: %s" % (d.get("issue"), d.get("description")) for d in body.get("details", [])]
        detail = "; ".join(parts) or str(body.get("message") or "")
    except ValueError:
        detail = resp.text[:200]
    msg = ("%s failed (HTTP %s) %s %s" % (what, resp.status_code, name or "", detail)).strip()
    raise PayPalError(msg, resp.headers.get("PayPal-Debug-Id"), name)


def _headers(token):
    return {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "PayPal-Request-Id": uuid.uuid4().hex,
    }


def get_token():
    if not config.PAYPAL_CLIENT_ID or not config.PAYPAL_CLIENT_SECRET:
        raise PayPalError("PayPal credentials are missing. Fill PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env")
    resp = requests.post(
        config.PAYPAL_BASE_URL + "/v1/oauth2/token",
        auth=(config.PAYPAL_CLIENT_ID, config.PAYPAL_CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        _raise(resp, "Access token request")
    return resp.json()["access_token"]


def _purchase_unit(amount, currency, reference):
    return {
        "reference_id": reference,
        "description": "SpendWarden purchase " + reference,
        "amount": {"currency_code": currency, "value": amount},
    }


def charge_vault(token, vault_id, amount, currency, reference):
    """Charge a saved payment token. No redirect, no payer step."""
    body = {
        "intent": "CAPTURE",
        "purchase_units": [_purchase_unit(amount, currency, reference)],
        "payment_source": {"paypal": {"vault_id": vault_id}},
    }
    resp = requests.post(config.PAYPAL_BASE_URL + "/v2/checkout/orders", headers=_headers(token), json=body, timeout=TIMEOUT)
    if resp.status_code not in (200, 201):
        _raise(resp, "Vault charge")
    return resp.json()


def create_order_for_approval(token, amount, currency, reference):
    """Create an order the buyer must approve. Returns (order_id, approve_url)."""
    body = {
        "intent": "CAPTURE",
        "purchase_units": [_purchase_unit(amount, currency, reference)],
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
    resp = requests.post(config.PAYPAL_BASE_URL + "/v2/checkout/orders", headers=_headers(token), json=body, timeout=TIMEOUT)
    if resp.status_code not in (200, 201):
        _raise(resp, "Create order")
    data = resp.json()
    for link in data.get("links", []):
        if link.get("rel") in ("payer-action", "approve"):
            return data["id"], link["href"]
    raise PayPalError("Create order returned no approval link")


def capture(token, order_id):
    resp = requests.post(
        "%s/v2/checkout/orders/%s/capture" % (config.PAYPAL_BASE_URL, order_id),
        headers=_headers(token),
        json={},
        timeout=TIMEOUT,
    )
    if resp.status_code not in (200, 201):
        _raise(resp, "Capture")
    return resp.json()


def summarize(result):
    out = {"order_id": result.get("id"), "order_status": result.get("status"), "capture_id": None, "capture_status": None}
    try:
        cap = result["purchase_units"][0]["payments"]["captures"][0]
        out["capture_id"] = cap["id"]
        out["capture_status"] = cap["status"]
    except (KeyError, IndexError, TypeError):
        pass
    return out
