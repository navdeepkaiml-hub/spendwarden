"""MCP server. The agent connects here and never sees PayPal credentials.

Run (stdio):  python -m server.mcp_server
"""
try:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _MCPServer
except ImportError:  # mcp 2.x renamed it
    from mcp.server.mcpserver import MCPServer as _MCPServer

from typing import List

from server import core

mcp = _MCPServer("spendwarden")


@mcp.tool()
def propose_purchase(merchant: str, items: List[str], amount: str, currency: str = "USD", rationale: str = "") -> dict:
    """Ask SpendWarden to make a purchase. Returns the decision and the payment status.

    merchant: store name. items: what is being bought. amount: total as a string like "18.00".
    rationale: why this purchase serves the user's task.
    """
    try:
        return core.propose_purchase(merchant, items, amount, currency, rationale)
    except ValueError as exc:
        return {"status": "REJECTED_INVALID", "error": str(exc)}


@mcp.tool()
def get_purchase_status(purchase_id: str) -> dict:
    """Look up a purchase by id."""
    try:
        return core.get_purchase(purchase_id)
    except LookupError as exc:
        return {"status": "NOT_FOUND", "error": str(exc)}


if __name__ == "__main__":
    mcp.run()
