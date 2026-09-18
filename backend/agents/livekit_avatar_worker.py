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

import asyncio
import json
import logging
import os
import re

import httpx
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
from openai.types.beta.realtime.session import InputAudioTranscription

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
# Same base URL agents/ask_costpilot_mcp_server.py uses -- this worker
# makes its own direct call to the real /ask endpoint (see
# _publish_full_answer below) purely to capture the FULL structured
# response (evidence, tables, proposal cards) for the browser's screen;
# the MCP tool call already asked the same question for the spoken
# answer, so this is a second, cheap, read-only call, not a different
# source of truth.
ASK_COSTPILOT_BASE_URL = os.getenv("ASK_COSTPILOT_BASE_URL", "http://localhost:8000")

# Matches the room-naming scheme api/routes_livekit.py's create_livekit_token
# mints: "ask-costpilot__<workspace_id>__<random>". Parsing it back out of
# the room name (rather than a fixed env var) is what lets this agent
# answer from whichever workspace the browser that started the call was
# actually looking at -- confirmed live 2026-09-19 the fixed-env-var
# version was a real bug: the avatar silently answered from an unrelated,
# sometimes far emptier workspace than the one the person meant.
_ROOM_WORKSPACE_RE = re.compile(r"^ask-costpilot__([A-Za-z0-9-]+)__")


def _workspace_id_from_room_name(room_name: str) -> str:
    match = _ROOM_WORKSPACE_RE.match(room_name or "")
    return match.group(1) if match else ""


def _build_avatar_instructions(workspace_id: str) -> str:
    workspace_clause = (
        f'The CostPilot workspace_id for this conversation is exactly "{workspace_id}" -- '
        f"always pass workspace_id=\"{workspace_id}\" on every ask_costpilot call, never omit "
        "it and never use a different value, even if the person names a different workspace out "
        "loud (tell them you can't switch workspaces mid-call instead)."
        if workspace_id
        else "This conversation's room didn't specify a workspace_id -- ask_costpilot will say "
        "so plainly if asked a data question; don't guess a workspace."
    )
    return f"""You are CostPilot's voice avatar -- a spoken, face-to-face version of Ask
CostPilot. You have exactly one source of truth: the ask_costpilot tool. Every fact, figure, or
claim about spend, budgets, agents, departments, accounts, or business outcomes must come from
calling that tool -- never answer a data question from your own knowledge, and never estimate,
round differently, or restate a number in a way that changes it.
Always speak in English, regardless of what language the person talks to you in -- never switch
languages to match them, and never let it drift partway through a call. This app's whole UI and
every other surface's answers are English-only, so a non-English reply here has nowhere correct
to go.
{workspace_clause}
Call ask_costpilot with the person's question close to verbatim. When it returns an answer, speak
it back in your own natural spoken phrasing -- CostPilot's own answer already contains the real,
checked numbers; your job is to say them naturally out loud, not to recompute or embellish them.
If ask_costpilot's answer says it doesn't know something or couldn't find data, say that plainly
too -- never fill the gap with a guess.
For anything that isn't a question about CostPilot's own governed data (small talk, "what can you
do", technical questions about the product), you may answer conversationally without calling the
tool."""


async def _publish_full_answer(ctx: JobContext, workspace_id: str, question: str) -> None:
    """
    Confirmed live 2026-09-19: talking to the avatar produced a spoken
    answer with NOTHING shown on screen at all -- no evidence, no table,
    no proposal card, none of the visual grounding every other Ask
    CostPilot surface has. The MCP tool's own return value is a
    spoken-friendly string only (see ask_costpilot_mcp_server.py's own
    docstring on why -- it's meant for a voice model to say aloud, not
    for structured rendering). Rather than change that contract, this
    makes its own direct call to the real endpoint for the full JSON
    (title/evidence/table/proposal), then pushes it to the browser over
    the room's data channel -- the frontend (ask-costpilot-livekit-
    avatar.js's onAnswer callback) renders it with the exact same
    renderAskAnswerCard() a typed question already uses, so a live-call
    answer looks like every other Ask CostPilot answer, not a special case.
    """
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.post(
                f"{ASK_COSTPILOT_BASE_URL}/api/reports/bot-efficiency/ask",
                json={"question": question, "workspace_id": workspace_id, "modality": "voice"},
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.warning("Couldn't fetch the full answer to publish to the room: %s", exc)
        return
    try:
        await ctx.room.local_participant.publish_data(
            json.dumps(data).encode("utf-8"), topic="ask-costpilot-answer",
        )
    except Exception as exc:
        logger.warning("Couldn't publish the full answer to the room: %s", exc)


def _register_answer_publisher(ctx: JobContext, session: AgentSession, workspace_id: str) -> None:
    def on_function_tools_executed(event) -> None:
        calls_by_id = {call.call_id: call for call in event.function_calls}
        for output in event.function_call_outputs:
            call = calls_by_id.get(output.call_id)
            if not call or "ask_costpilot" not in call.name:
                continue
            try:
                question = json.loads(call.arguments or "{}").get("question", "")
            except (TypeError, ValueError):
                question = ""
            if not question:
                continue
            asyncio.create_task(_publish_full_answer(ctx, workspace_id, question))

    session.on("function_tools_executed", on_function_tools_executed)


async def entrypoint(ctx: JobContext):
    simli_api_key = os.getenv("SIMLI_API_KEY")
    simli_face_id = os.getenv("SIMLI_FACE_ID")
    if not simli_api_key or not simli_face_id:
        raise RuntimeError(
            "SIMLI_API_KEY and SIMLI_FACE_ID must both be set -- see this module's docstring."
        )

    workspace_id = _workspace_id_from_room_name(ctx.room.name)
    if not workspace_id:
        logger.warning(
            "Room %r didn't encode a workspace_id (ask-costpilot__<id>__<random>) -- "
            "ask_costpilot will fall back to ASK_COSTPILOT_MCP_WORKSPACE_ID, if set.",
            ctx.room.name,
        )

    session = AgentSession(
        llm=openai.realtime.RealtimeModel(
            voice=REALTIME_VOICE,
            # Pins the input-side transcription language the same way
            # routes_ask_voice.py's Whisper call already does -- without
            # this the model can mis-detect the caller's language on a
            # noisy/short utterance and then reply in that language for
            # the rest of the call, on top of the instructions-level
            # English directive below.
            input_audio_transcription=InputAudioTranscription(language="en"),
        ),
    )

    simli_avatar = simli.AvatarSession(
        simli_config=simli.SimliConfig(
            api_key=simli_api_key,
            face_id=simli_face_id,
        ),
    )
    await simli_avatar.start(session, room=ctx.room)
    _register_answer_publisher(ctx, session, workspace_id)

    await session.start(
        agent=Agent(
            instructions=_build_avatar_instructions(workspace_id),
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
