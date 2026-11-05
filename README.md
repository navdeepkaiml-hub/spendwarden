# SpendWarden

A spending firewall for AI agents, built on the PayPal sandbox.

SpendWarden sits between an AI agent and PayPal. The agent never holds payment
credentials. Every purchase is checked against hard limits, verified against what the
user actually asked for, and recorded in a tamper-evident audit log.

Status: Day 4 of the PayPal AI Hackathon build. Purchases go through deterministic policy rules,
then an LLM intent verifier that compares the purchase with the task the user set (a mandate). The
verifier treats everything the agent read as untrusted and can only make a decision stricter. Larger
purchases wait for a human. The dashboard and red-team suite are next.

## Quickstart (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
notepad .env                                   # PayPal sandbox + Gemini credentials
.\.venv\Scripts\python scripts\paypal_vault_spike.py   # one-time buyer consent (vault mode)
.\.venv\Scripts\python -m pytest -q            # tests run offline, PayPal is mocked
.\.venv\Scripts\python -m uvicorn server.main:app --reload
```

In a second window:

```powershell
.\.venv\Scripts\python scripts\try_purchase.py --amount 18.00
.\.venv\Scripts\python scripts\demo_scenes.py      # normal purchase, blocked attack, human approval
```

Poisoned demo store and demo agent (two more windows):

```powershell
.\.venv\Scripts\python demo\store\app.py                                   # http://127.0.0.1:8100
.\.venv\Scripts\python demo\agent\shopper.py --mode compromised --page mug-upsell
.\.venv\Scripts\python demo\agent\shopper.py --mode llm --page mug-promo     # a real Gemini agent
```

Useful endpoints: `/v1/mandates`, `/v1/policy`, `/v1/budget`, `/v1/approvals`, `/v1/purchases/{id}/approve`.

API docs: http://127.0.0.1:8000/docs  ·  Ledger check: http://127.0.0.1:8000/v1/audit/verify

MCP server for agents (stdio): `python -m server.mcp_server`

macOS / Linux: replace `.\.venv\Scripts\python` with `.venv/bin/python` and `copy` with `cp`.

## Layout

- `server/` - REST API, MCP server, core logic, PayPal client, ledger
- `tests/` - offline tests
- `scripts/` - setup spikes and a purchase client
- `demo/` - poisoned demo store, demo agent, recorded verifier verdicts for replay mode
- `dashboard/`, `redteam/` - planned
- `docs/` - notes and architecture

## License

Apache-2.0. See `LICENSE`.
