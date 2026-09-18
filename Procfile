release: bash scripts/release.sh
web: cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1
worker: cd backend && python scripts/run_cdc_subscriber.py
# One dyno hosts both the MCP tool server (agents/ask_costpilot_mcp_server.py,
# backgrounded, reachable at ASK_COSTPILOT_MCP_URL -- defaults to
# http://127.0.0.1:8100/mcp, matching this same-dyno setup) and the LiveKit
# agent worker (agents/livekit_avatar_worker.py) that connects to it --
# deliberately one new dyno, not two, since the MCP server is a lightweight
# stateless HTTP wrapper with no reason to scale independently of the worker
# that's its only caller.
livekit-avatar: cd backend && (python -m agents.ask_costpilot_mcp_server &) && python -m agents.livekit_avatar_worker start
