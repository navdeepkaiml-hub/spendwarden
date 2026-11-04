"""REST API. Run:  python -m uvicorn server.main:app --reload"""
from typing import List, Optional, Union

from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel, Field

from server import config, core, ledger, policy

app = FastAPI(title="SpendWarden", version="0.2.0")


class PurchaseIn(BaseModel):
    merchant: str
    items: List[str] = Field(default_factory=list)
    amount: Union[str, int, float]
    currency: str = "USD"
    rationale: str = ""


@app.get("/health")
def health():
    return {"ok": True, "payment_mode": config.PAYMENT_MODE}


@app.post("/v1/purchases")
def create_purchase(body: PurchaseIn):
    try:
        return core.propose_purchase(body.merchant, body.items, body.amount, body.currency, body.rationale)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/v1/purchases")
def list_purchases(limit: int = 50, status: Optional[str] = None):
    return core.list_purchases(limit, status)


@app.get("/v1/purchases/{purchase_id}")
def read_purchase(purchase_id: str):
    try:
        return core.get_purchase(purchase_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


def _transition(fn, purchase_id):
    try:
        return fn(purchase_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/v1/approvals")
def pending_approvals():
    return core.list_purchases(100, "AWAITING_APPROVAL")


@app.post("/v1/purchases/{purchase_id}/approve")
def approve_purchase(purchase_id: str):
    return _transition(core.approve, purchase_id)


@app.post("/v1/purchases/{purchase_id}/reject")
def reject_purchase(purchase_id: str):
    return _transition(core.reject, purchase_id)


@app.post("/v1/purchases/{purchase_id}/capture")
def capture_purchase(purchase_id: str):
    return _transition(core.capture_approved, purchase_id)


@app.get("/v1/policy")
def read_policy():
    return policy.get_policy()


@app.put("/v1/policy")
def update_policy(body: dict = Body(...)):
    try:
        return policy.set_policy(body, who="api")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/v1/budget")
def read_budget():
    return policy.budget_status()


@app.get("/v1/audit/verify")
def verify_audit():
    return ledger.verify()
