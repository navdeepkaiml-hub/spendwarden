"""REST API and dashboard. Run:  python -m uvicorn server.main:app --reload

Access control (see config.py): with ADMIN_TOKEN / AGENT_TOKEN unset everything is open (local development).
When set, admin routes need X-Admin-Token and the agent routes need X-Agent-Token (or the admin token).
"""
import secrets
from pathlib import Path
from typing import List, Optional, Union

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from server import config, core, demo, ledger, policy

app = FastAPI(title="SpendWarden", version="0.4.0")
DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"


def _same(given, expected):
    return bool(given) and secrets.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def require_admin(x_admin_token: Optional[str] = Header(default=None)):
    if config.ADMIN_TOKEN and not _same(x_admin_token or "", config.ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="admin token required")


def require_agent(x_agent_token: Optional[str] = Header(default=None),
                  x_admin_token: Optional[str] = Header(default=None)):
    if not config.AGENT_TOKEN:
        return
    if _same(x_agent_token or "", config.AGENT_TOKEN):
        return
    if config.ADMIN_TOKEN and _same(x_admin_token or "", config.ADMIN_TOKEN):
        return
    raise HTTPException(status_code=401, detail="agent token required")


class PurchaseIn(BaseModel):
    merchant: str
    items: List[str] = Field(default_factory=list)
    amount: Union[str, int, float]
    currency: str = "USD"
    rationale: str = ""
    mandate_id: Optional[str] = None
    evidence: List[str] = Field(default_factory=list)


class MandateIn(BaseModel):
    task: str


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/ui/")


@app.get("/health")
def health():
    return {"ok": True, "payment_mode": config.PAYMENT_MODE, "verifier_mode": config.VERIFIER_MODE,
            "auth_required": bool(config.ADMIN_TOKEN), "demo_mode": config.DEMO_MODE}


# ---- agent routes
@app.post("/v1/purchases", dependencies=[Depends(require_agent)])
def create_purchase(body: PurchaseIn):
    try:
        return core.propose_purchase(body.merchant, body.items, body.amount, body.currency, body.rationale,
                                     body.mandate_id, body.evidence)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/v1/purchases/{purchase_id}", dependencies=[Depends(require_agent)])
def read_purchase(purchase_id: str):
    try:
        return core.get_purchase(purchase_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/v1/budget", dependencies=[Depends(require_agent)])
def read_budget():
    return policy.budget_status()


# ---- admin routes
@app.get("/v1/purchases", dependencies=[Depends(require_admin)])
def list_purchases(limit: int = 50, status: Optional[str] = None):
    return core.list_purchases(limit, status)


@app.get("/v1/purchases/{purchase_id}/receipt", dependencies=[Depends(require_admin)])
def read_receipt(purchase_id: str):
    try:
        return core.receipt(purchase_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


def _transition(fn, purchase_id):
    try:
        return fn(purchase_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/v1/approvals", dependencies=[Depends(require_admin)])
def pending_approvals():
    return core.list_purchases(100, "AWAITING_APPROVAL")


@app.post("/v1/purchases/{purchase_id}/approve", dependencies=[Depends(require_admin)])
def approve_purchase(purchase_id: str):
    return _transition(core.approve, purchase_id)


@app.post("/v1/purchases/{purchase_id}/reject", dependencies=[Depends(require_admin)])
def reject_purchase(purchase_id: str):
    return _transition(core.reject, purchase_id)


@app.post("/v1/purchases/{purchase_id}/capture", dependencies=[Depends(require_admin)])
def capture_purchase(purchase_id: str):
    return _transition(core.capture_approved, purchase_id)


@app.post("/v1/mandates", dependencies=[Depends(require_admin)])
def create_mandate(body: MandateIn):
    try:
        return core.create_mandate(body.task)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/v1/mandates", dependencies=[Depends(require_admin)])
def list_mandates():
    return core.list_mandates()


@app.get("/v1/mandates/{mandate_id}", dependencies=[Depends(require_admin)])
def read_mandate(mandate_id: str):
    try:
        return core.get_mandate(mandate_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/v1/policy", dependencies=[Depends(require_admin)])
def read_policy():
    return policy.get_policy()


@app.put("/v1/policy", dependencies=[Depends(require_admin)])
def update_policy(body: dict = Body(...)):
    try:
        return policy.set_policy(body, who="dashboard")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/v1/audit", dependencies=[Depends(require_admin)])
def read_audit(limit: int = 200):
    return ledger.entries(limit=limit)


@app.get("/v1/audit/verify", dependencies=[Depends(require_admin)])
def verify_audit():
    return ledger.verify()


@app.post("/v1/demo/run", dependencies=[Depends(require_admin)])
def demo_run():
    if not config.DEMO_MODE:
        raise HTTPException(status_code=404, detail="demo mode is off")
    return {"scenes": demo.run_scenes()}


@app.post("/v1/demo/reset", dependencies=[Depends(require_admin)])
def demo_reset():
    if not config.DEMO_MODE:
        raise HTTPException(status_code=404, detail="demo mode is off")
    demo.reset()
    return {"ok": True}


if DASHBOARD_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="ui")
