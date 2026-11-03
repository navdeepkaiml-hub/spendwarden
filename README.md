# SpendWarden

A spending firewall for AI agents, built on the PayPal sandbox.

SpendWarden sits between an AI agent and PayPal. The agent never holds payment
credentials. Every purchase is checked against hard limits, verified against what the
user actually asked for, and recorded in a tamper-evident audit log.

Status: Day 2 of the PayPal AI Hackathon build. The walking skeleton works end to end
(agent request -> SpendWarden -> PayPal sandbox payment -> hash-chained ledger). The
policy engine and intent verifier are next; for now every purchase is allowed.

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
```

API docs: http://127.0.0.1:8000/docs  ·  Ledger check: http://127.0.0.1:8000/v1/audit/verify

MCP server for agents (stdio): `python -m server.mcp_server`

macOS / Linux: replace `.\.venv\Scripts\python` with `.venv/bin/python` and `copy` with `cp`.

## Layout

- `server/` - REST API, MCP server, core logic, PayPal client, ledger
- `tests/` - offline tests
- `scripts/` - setup spikes and a purchase client
- `dashboard/`, `demo/`, `redteam/` - planned
- `docs/` - notes and architecture

## License

Apache-2.0. See `LICENSE`.
