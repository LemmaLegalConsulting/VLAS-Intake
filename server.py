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
logger.add(sys.stderr, level=get_ev("LOG_LEVEL", "INFO"))
if ev_is_true("LOG_TO_FILE"):
    os.makedirs("logs", exist_ok=True)
    logger.add("logs/server.log", level=get_ev("LOG_LEVEL", "INFO"))


def generate_call_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")

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
    )
