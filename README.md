# SpendWarden

A spending firewall for AI agents, built on the PayPal sandbox.

SpendWarden sits between an AI agent and PayPal. The agent never holds payment
credentials. Every purchase is checked against hard limits, verified against what the
user actually asked for, and recorded in a tamper-evident audit log.

Status: Day 1 of the PayPal AI Hackathon build. Currently verifying the PayPal sandbox
payment path and the LLM connection.

## Quickstart (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
notepad .env          # paste your PayPal sandbox and Gemini credentials
.\.venv\Scripts\python scripts\gemini_check.py
.\.venv\Scripts\python scripts\paypal_spike.py
```

macOS / Linux: replace `.\.venv\Scripts\python` with `.venv/bin/python` and `copy` with `cp`.

## Layout

- `server/` - FastAPI app, MCP tools, policy engine, verifier, ledger (planned)
- `dashboard/` - web dashboard (planned)
- `demo/store/` - demo merchant store, including a poisoned page (planned)
- `demo/agent/` - demo shopping agent (planned)
- `redteam/` - prompt-injection and abuse test cases (planned)
- `scripts/` - setup and spike scripts
- `docs/` - notes and architecture

## License

Apache-2.0. See `LICENSE`.
