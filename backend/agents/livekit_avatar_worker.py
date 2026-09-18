"""
agents/livekit_avatar_worker.py — real-time video avatar for Ask
CostPilot: a LiveKit Agents worker running an OpenAI Realtime voice
model, rendered as a lip-synced Simli video avatar, grounded in
CostPilot's real data via the MCP tool server
(agents/ask_costpilot_mcp_server.py) instead of the model's own
knowledge.

This is a second, continuously-running system alongside the FastAPI web
app (see main.py) -- a persistent worker process, not a request handler.
It answers ONLY by calling the ask_costpilot MCP tool; every fact it
speaks must come from that tool's real, already-validated response, the
same discipline api/routes_efficiency.py's _ask_costpilot_agent prompt
already enforces for the text/turn-based agent loop.

Run (LiveKit Agents CLI, dev mode against a local/staging LiveKit
project):
    python -m agents.livekit_avatar_worker dev
Run in production (Heroku Procfile's livekit-avatar process):
    python -m agents.livekit_avatar_worker start

Required environment (see .env.example):
    LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET   -- LiveKit project
    SIMLI_API_KEY, SIMLI_FACE_ID                       -- Simli avatar
    OPENAI_API_KEY                                     -- already used elsewhere in this app
    ASK_COSTPILOT_MCP_URL   -- where ask_costpilot_mcp_server.py is reachable
                               (default http://127.0.0.1:8100/mcp, same
                               process host if run alongside this worker)
"""

import logging
import os

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    WorkerOptions,
    WorkerType,
    cli,
    mcp,
)
from livekit.plugins import openai, simli

logger = logging.getLogger("costpilot-livekit-avatar")
logger.setLevel(logging.INFO)

load_dotenv(override=True)

# "onyx" (this app's existing TTS voice, see api/routes_ask_voice.py) is
# NOT a valid Realtime API voice -- confirmed against the installed SDK
# (livekit-plugins-openai 1.8.2): the Realtime voice roster is
# aster/beacon/cinder/marin/stone/vesper, an entirely different set from
# the standard TTS API's alloy/echo/fable/onyx/nova/shimmer. "marin" is
# the SDK's own default; named here as one constant so it's a one-line
# change once you've actually heard it and have an opinion.
REALTIME_VOICE = os.getenv("ASK_COSTPILOT_AVATAR_VOICE", "marin")

ASK_COSTPILOT_MCP_URL = os.getenv("ASK_COSTPILOT_MCP_URL", "http://127.0.0.1:8100/mcp")

AVATAR_INSTRUCTIONS = """You are CostPilot's voice avatar -- a spoken, face-to-face version of Ask
CostPilot. You have exactly one source of truth: the ask_costpilot tool. Every fact, figure, or
claim about spend, budgets, agents, departments, accounts, or business outcomes must come from
calling that tool -- never answer a data question from your own knowledge, and never estimate,
round differently, or restate a number in a way that changes it.
Call ask_costpilot with the person's question close to verbatim. When it returns an answer, speak
it back in your own natural spoken phrasing -- CostPilot's own answer already contains the real,
checked numbers; your job is to say them naturally out loud, not to recompute or embellish them.
If ask_costpilot's answer says it doesn't know something or couldn't find data, say that plainly
too -- never fill the gap with a guess.
For anything that isn't a question about CostPilot's own governed data (small talk, "what can you
do", technical questions about the product), you may answer conversationally without calling the
tool."""


async def entrypoint(ctx: JobContext):
    simli_api_key = os.getenv("SIMLI_API_KEY")
    simli_face_id = os.getenv("SIMLI_FACE_ID")
    if not simli_api_key or not simli_face_id:
        raise RuntimeError(
            "SIMLI_API_KEY and SIMLI_FACE_ID must both be set -- see this module's docstring."
        )

    session = AgentSession(
        llm=openai.realtime.RealtimeModel(voice=REALTIME_VOICE),
    )

    simli_avatar = simli.AvatarSession(
        simli_config=simli.SimliConfig(
            api_key=simli_api_key,
            face_id=simli_face_id,
        ),
    )
    await simli_avatar.start(session, room=ctx.room)

    await session.start(
        agent=Agent(
            instructions=AVATAR_INSTRUCTIONS,
            # mcp_servers= is deprecated (confirmed live: logs a
            # DeprecationWarning) in favor of wrapping the MCP server in a
            # Toolset and passing it through tools= instead.
            tools=[mcp.MCPToolset(
                id="ask-costpilot",
                mcp_server=mcp.MCPServerHTTP(url=ASK_COSTPILOT_MCP_URL),
            )],
        ),
        room=ctx.room,
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, worker_type=WorkerType.ROOM))
