"""REST API. Run:  python -m uvicorn server.main:app --reload"""
from typing import List, Union

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from server import config, core, ledger

app = FastAPI(title="SpendWarden", version="0.1.0")


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
def list_purchases(limit: int = 50):
    return core.list_purchases(limit)


@app.get("/v1/purchases/{purchase_id}")
def read_purchase(purchase_id: str):
    try:
        return core.get_purchase(purchase_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/v1/purchases/{purchase_id}/capture")
def capture_purchase(purchase_id: str):
    try:
        return core.capture_approved(purchase_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/v1/audit/verify")
def verify_audit():
    return ledger.verify()
