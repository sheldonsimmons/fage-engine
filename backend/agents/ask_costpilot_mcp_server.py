"""
agents/ask_costpilot_mcp_server.py — MCP server wrapping Ask CostPilot's
real answer endpoint, so a LiveKit realtime voice agent (see
agents/livekit_avatar_worker.py) can call it as a tool instead of
answering from its own knowledge.

Deliberately calls the real, already-running app over HTTP
(POST /api/reports/bot-efficiency/ask) rather than importing
ask_costpilot() and its DB-session machinery in-process -- this is the
exact same contract every other Ask CostPilot surface (the desktop
drawer, ask-voice.html, a typed question) already uses, so this tool
can never drift from what a person asking the same question directly
would get. One tool, ask_costpilot, deliberately narrow: this MCP
server has no other capabilities and should never grow report-shaped
tools of its own -- CostPilot's real answer engine (with its evidence
checks, numeric-fidelity validation, and department scoping) already
lives behind that one endpoint.

Run standalone for local testing:
    python -m agents.ask_costpilot_mcp_server
Serves streamable-http on ASK_COSTPILOT_MCP_HOST:ASK_COSTPILOT_MCP_PORT
(defaults 127.0.0.1:8100) -- agents/livekit_avatar_worker.py points its
MCPServerHTTP client at this same host:port.
"""

import os

import httpx
from mcp.server.fastmcp import FastMCP

# The real, already-running FastAPI app's own base URL -- this MCP server
# is a thin HTTP client of it, never a second implementation of any
# answer logic. Must be set to the deployed app's real URL in production
# (the LiveKit agent worker runs as its own Heroku dyno, separate from
# `web`, so "localhost" only works for local development where both
# processes happen to run on the same machine).
ASK_COSTPILOT_BASE_URL = os.getenv("ASK_COSTPILOT_BASE_URL", "http://localhost:8000")

# Last-resort fallback only -- confirmed live 2026-09-19 that relying on
# this as the ONLY source of workspace_id is a real bug, not just a v1
# simplification: the browser's actual workspace (whatever the person is
# really looking at, switchable via the workspace dropdown) was never
# threaded through the room at all, so the avatar silently answered from
# whatever this env var happened to be set to -- unrelated to, and
# sometimes emptier than, the workspace the person actually meant.
# agents/livekit_avatar_worker.py now resolves the real workspace_id from
# the LiveKit room name (encoded there at token-mint time, see
# api/routes_livekit.py) and passes it as a real tool argument on every
# call -- this env var is now only a fallback for local/manual testing of
# this MCP server on its own, outside a real room.
ASK_COSTPILOT_MCP_DEFAULT_WORKSPACE_ID = os.getenv("ASK_COSTPILOT_MCP_WORKSPACE_ID", "")

mcp = FastMCP(
    name="ask-costpilot",
    host=os.getenv("ASK_COSTPILOT_MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("ASK_COSTPILOT_MCP_PORT", "8100")),
)


@mcp.tool()
async def ask_costpilot(question: str, workspace_id: str = "") -> str:
    """
    Ask CostPilot's real, governed-data answer engine a question about AI
    spend, usage, budgets, agents, departments, accounts, or business
    outcomes. Returns CostPilot's own plain-English answer, already
    computed from real data -- never answer a question about CostPilot's
    data yourself; always call this tool and speak back exactly what it
    says, in your own natural spoken phrasing, without inventing,
    rounding, or adding any figure this tool did not return.

    workspace_id: always pass the exact workspace_id given in your system
    instructions for this conversation -- never omit it and never guess
    a different one.
    """
    resolved_workspace_id = workspace_id or ASK_COSTPILOT_MCP_DEFAULT_WORKSPACE_ID
    if not resolved_workspace_id:
        return (
            "CostPilot isn't configured with a workspace for this voice session yet -- "
            "tell the person asking that this avatar isn't fully set up rather than guessing an answer."
        )
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.post(
                f"{ASK_COSTPILOT_BASE_URL}/api/reports/bot-efficiency/ask",
                json={
                    "question": question,
                    "workspace_id": resolved_workspace_id,
                    "modality": "voice",
                },
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        return f"CostPilot couldn't answer that just now ({exc}) -- say so plainly, don't guess a number."
    answer = data.get("answer")
    if not answer:
        return "CostPilot returned no answer for that question -- say so plainly, don't guess a number."
    return answer


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
