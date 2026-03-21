import os
import sys
from datetime import UTC, datetime

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from intake_bot.bot import run_bot
from intake_bot.nodes.nodes import node_initial
from intake_bot.utils.ev import ev_is_true, get_ev
from loguru import logger
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

logger.remove(0)

# Suppress noisy pipecat DEBUG logs from turn-detection internals.
_NOISY_PIPECAT_MODULES = {
    "pipecat.turns.user_start",
    "pipecat.audio.turn.smart_turn",
}


def _log_filter(record):
    if record["level"].name == "DEBUG":
        name = record["name"] or ""
        for prefix in _NOISY_PIPECAT_MODULES:
            if name.startswith(prefix):
                return False
    return True


logger.add(sys.stderr, level=get_ev("LOG_LEVEL", "INFO"), filter=_log_filter)
if ev_is_true("LOG_TO_FILE"):
    os.makedirs("logs", exist_ok=True)
    logger.add("logs/server.log", level=get_ev("LOG_LEVEL", "INFO"), filter=_log_filter)


def generate_call_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _get_user_idle_timeout_secs(websocket: WebSocket, call_id: str) -> float | None:
    raw_timeout = websocket.query_params.get("idle_timeout_secs")
    if raw_timeout is None:
        raw_timeout = get_ev("WEBSOCKET_USER_IDLE_TIMEOUT_SECS", "").strip()
    if raw_timeout is None or raw_timeout == "":
        if call_id.startswith("ws-test"):
            raw_timeout = get_ev("WEBSOCKET_TEST_USER_IDLE_TIMEOUT_SECS", "45.0")
    if not raw_timeout:
        return None

    try:
        timeout_secs = float(raw_timeout)
    except ValueError:
        logger.warning(
            f"""Ignoring invalid websocket idle timeout value: {raw_timeout!r}"""
        )
        return None

    if timeout_secs <= 0:
        logger.warning(
            f"""Ignoring non-positive websocket idle timeout value: {raw_timeout!r}"""
        )
        return None

    return timeout_secs


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    caller_phone_number = websocket.query_params.get("caller_phone_number", "")
    call_id = websocket.query_params.get("call_id") or generate_call_id()
    user_idle_timeout_secs = _get_user_idle_timeout_secs(websocket, call_id)

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=ProtobufFrameSerializer(),
        ),
    )

    async def configure_websocket_transport(transport, task, flow_manager, call_id):
        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport, client):
            logger.info(f"""WebSocket client connected for call {call_id}""")
            await flow_manager.initialize(node_initial())

    await run_bot(
        transport=transport,
        call_id=call_id,
        caller_phone_number=caller_phone_number,
        handle_sigint=False,
        configure_transport=configure_websocket_transport,
        user_idle_timeout_secs=user_idle_timeout_secs,
    )
