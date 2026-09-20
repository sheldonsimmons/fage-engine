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
from openai.types.beta.realtime.session import InputAudioTranscription, TurnDetection

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
Before calling ask_costpilot, always say a brief acknowledgment out loud first -- e.g. "Let me
check that," "One moment," "Looking that up now." That lookup can take several seconds, and
silence during that wait makes a person think the call dropped or didn't hear them at all. Never
go straight from hearing a question to dead air.
Call ask_costpilot with the person's question close to verbatim, EVERY time a data question is
asked -- including a question that sounds similar to, or builds on, one you already answered
earlier in this same call. Never answer a new question from a tool result you already have in
memory; a different department, metric, time window, or follow-up nuance can change the real
number even when the wording looks almost the same, and only the tool knows which.
Call it exactly ONCE per question, with the question passed close to verbatim -- never split one
question into several smaller calls (e.g. one call per department in a comparison), and never
call it again to "double check" or fill in more detail for a question you already called it for.
Confirmed live: asked to "compare Finance and Engineering... and create a report," the tool was
called three separate times for that one question with reworded phrasing each time, one of which
even resolved to a different, narrower kind of answer than a real comparison -- then the reply
blended pieces from all three calls into something broader than any single one of them, which is
exactly the over-wide "report" behavior this section is trying to prevent. One call, close to the
person's actual words, is both correct and sufficient.
When it returns an answer, speak it back in your own natural spoken phrasing -- CostPilot's own
answer already contains the real, checked numbers; your job is to say them naturally out loud, not
to recompute or invent anything. Answer exactly what was asked first, using exactly the figures
ask_costpilot returned -- confirmed live: asked to "compare Finance and Engineering and create a
report," the tool correctly returned a two-department comparison, but the spoken answer turned
into a wider-ranging report covering more than what was actually asked or returned. A person
saying "report" or "give me the full picture" is asking for that same answer delivered thoroughly,
not for you to broaden or pad it with unrequested detail.
Once the actual question is answered, you may add ONE brief, clearly-flagged follow-up
observation if it's genuinely relevant and useful -- e.g. "for context, that's higher than most
other departments this month" or "worth noting, Engineering is close to its cap too" -- but ONLY
if it comes from calling ask_costpilot again (or a value already in an earlier tool result this
call), never a number you're inferring or guessing. Never let that addition replace, overshadow,
or get spoken before the direct answer itself.
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
            # Confirmed live: passing `language` alone made the Realtime
            # API reject the whole session ("Missing required parameter:
            # 'session.audio.input.transcription.model'") -- once
            # `language` is set, `model` becomes required too.
            input_audio_transcription=InputAudioTranscription(
                model="gpt-4o-mini-transcribe", language="en",
            ),
            # Confirmed live 2026-09-20: a long "compare X and Y, provide
            # a report" answer got cut off mid-response --
            # "OpenAI Realtime API returned an error: RealtimeError(
            # message='Audio content of 7700ms is already shorter than
            # 14079ms')" immediately followed by "speech not done in
            # time after interruption, cancelling the speech
            # arbitrarily." The default server_vad turn detector is
            # amplitude-based and trigger-happy on a live server-side
            # session with no local echo cancellation, and a false
            # mid-sentence interruption on a long answer desyncs the
            # server's and client's idea of how much audio actually
            # played -- the same underlying class of bug that cut off
            # the greeting, just mid-answer instead of at call start.
            # semantic_vad waits for an actual pause in meaning rather
            # than a brief amplitude dip, which is far less prone to
            # firing on room noise mid-sentence -- confirmed live this
            # alone wasn't enough, though: the exact same "Audio content
            # of Xms is already shorter than Yms" / "speech not done in
            # time after interruption, cancelling the speech arbitrarily"
            # error recurred (2026-09-20 13:22) even with semantic_vad
            # active. interrupt_response=False is the actual server-side
            # OpenAI Realtime session setting for this (distinct from
            # generate_reply's allow_interruptions, which the SDK
            # rejects for a RealtimeModel -- see the greeting comment
            # below) -- it stops user speech from interrupting the
            # model's current response at all, removing the race
            # entirely instead of just making it rarer. Trade-off: no
            # barge-in while the avatar is mid-answer, only once it's
            # done -- an acceptable cost against a bug that kept
            # actually breaking real answers.
            turn_detection=TurnDetection(
                type="semantic_vad", eagerness="low", interrupt_response=False,
            ),
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
                mcp_server=mcp.MCPServerHTTP(
                    url=ASK_COSTPILOT_MCP_URL,
                    # Confirmed live via the worker's own traceback: a
                    # real ask_costpilot call was cancelled with
                    # "deadline exceeded" inside the MCP client's own
                    # request handling, well before our httpx call to
                    # /ask (which has its own 25s timeout, see
                    # ask_costpilot_mcp_server.py) could return -- the
                    # agent_tool_loop path this app already logs taking
                    # 10-17s for real questions blows straight through
                    # MCPServerHTTP's 5-second defaults for both
                    # `timeout` and `client_session_timeout_seconds`,
                    # so the realtime model gave up and told the person
                    # it "couldn't find the data" even though the real
                    # answer was still being computed. 30s covers the
                    # slowest real calls with headroom.
                    timeout=30,
                    client_session_timeout_seconds=30,
                ),
            )],
        ),
        room=ctx.room,
    )

    # Realtime models don't speak first on their own -- a person joining
    # this call previously had no signal the avatar was even live besides
    # the video appearing, confirmed live to read as "did it hear me?"
    # tool_choice="none" keeps this one reply from calling ask_costpilot
    # (there's no real question to answer yet).
    #
    # allow_interruptions=False was tried here first (to stop the
    # greeting getting cut off, see the turn_detection comment above) but
    # confirmed live it's silently rejected for a RealtimeModel: "the
    # RealtimeModel uses a server-side turn detection, allow_interruptions
    # cannot be False when using VoiceAgent.generate_reply(), disable
    # turn_detection in the RealtimeModel and use VAD on the
    # AgentTask/VoiceAgent instead" -- doing that would mean running our
    # own local VAD instead of the Realtime API's, a much bigger change
    # than a one-sentence greeting justifies. Relying instead on the
    # semantic_vad + eagerness="low" turn_detection above (added for the
    # same underlying false-interruption bug, just triggering mid-answer
    # instead of at call start) to make a false trigger here rare enough
    # not to matter.
    # Confirmed live 2026-09-20: the greeting still isn't consistently
    # heard even after removing the invalid allow_interruptions param and
    # switching to semantic_vad -- rather than guess at a third fix
    # blind, this logs the SpeechHandle's own outcome (done/interrupted/
    # exception) so the next real test gives direct proof of what
    # actually happens to this call instead of more speculation.
    try:
        greeting_handle = await session.generate_reply(
            instructions=(
                "Greet the person warmly in one short sentence. Say the word \"CostPilot\" out loud "
                "as part of the greeting itself (e.g. \"Hi, I'm CostPilot\" or \"You've got CostPilot\") "
                "-- don't just imply who you are, actually say the name -- and invite them to ask "
                "about their AI spend, budgets, or usage. Do not call any tool."
            ),
            tool_choice="none",
        )
        await greeting_handle.wait_for_playout()
        logger.info(
            "greeting speech handle finished: interrupted=%s exception=%s",
            greeting_handle.interrupted, greeting_handle.exception,
        )
    except Exception:
        logger.exception("greeting generate_reply raised")


AVATAR_AGENT_NAME = "ask-costpilot-avatar"


if __name__ == "__main__":
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        worker_type=WorkerType.ROOM,
        # Confirmed live: with agent_name unset (pure "automatic dispatch"),
        # this worker registered with LiveKit Cloud successfully every
        # time, but never once received a "received job request" log for
        # any room the browser actually joined -- LiveKit Cloud projects
        # route jobs via explicit dispatch, which requires the worker to
        # register under a name AND the room's token to request that name
        # (see api/routes_livekit.py's RoomAgentDispatch). Without both
        # halves, the server never offers this worker anything to do, no
        # matter how much memory or CPU it has -- this was never a
        # resource problem.
        agent_name=AVATAR_AGENT_NAME,
        # livekit-agents keeps this many full pre-warmed Python processes
        # (each with the openai/simli plugins already loaded) on standby
        # in production so a job never waits on cold-start -- its default
        # of 4 is sized for real concurrent traffic, not a single-user
        # pilot, and confirmed live to be what pushed a 512MB dyno to
        # 125%+ memory at idle (Error R14) before a single call ever
        # connected. Dropping to 1 (confirmed live: 650MB -> 528MB) still
        # left the dyno just over quota -- 0 removes the standby process
        # entirely, trading a few seconds of cold-start latency on the
        # first call after each boot/idle stretch for a dyno that isn't
        # permanently over its memory limit.
        num_idle_processes=0,
        # WorkerOptions' production default load_threshold is 0.7 -- the
        # worker self-reports "unavailable" to LiveKit's server above
        # that, and an unavailable worker is offered zero jobs. Confirmed
        # live via a controlled test (minted a token, joined the room
        # directly, confirmed via the LiveKit API that a real dispatch +
        # job was created for it): no worker ever claimed that job, and
        # this dyno's own logs show it repeatedly self-reporting load
        # 0.7-0.98 and "marking as unavailable" seconds after every
        # single boot, with no real call in progress -- explaining every
        # prior "wrong workspace" / "no data" report: the worker was
        # simply never being offered the room at all. Raising this to
        # 0.95 helped but confirmed live 2026-09-20 it still wasn't
        # enough -- the worker's self-reported load hit 0.99-1.93 at
        # idle (this default load calc appears to sum CPU across cores
        # on this dyno, so >1.0 is a normal reading here, not overload)
        # and got stuck "unavailable" long enough to silently drop two
        # separate real connection attempts in a row. This dyno runs
        # the FastAPI app, the MCP server, and this worker together for
        # single-user pilot traffic, where a dropped call is a much
        # worse failure than a slow one -- removing the ceiling
        # entirely rather than continuing to guess at a number high
        # enough to never trip it.
        load_threshold=float("inf"),
    ))
