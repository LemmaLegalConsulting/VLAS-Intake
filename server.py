import asyncio
import json
import sys
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import FastAPI, WebSocket, WebSocketException
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pipecat.audio.mixers.base_audio_mixer import BaseAudioMixer
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from starlette.status import WS_1008_POLICY_VIOLATION

from intake_bot.bot import run_bot, schedule_flow_initialization
from intake_bot.nodes.nodes import node_start
from intake_bot.utils.call_logging import runtime_log_filter
from intake_bot.utils.ev import get_ev

logger.remove(0)
logger.add(
    sys.stderr,
    level=get_ev("LOG_LEVEL", "INFO"),
    filter=cast(Any, runtime_log_filter),
)


_rate_limit_store: dict[str, list[float]] = {}
_rate_limit_lock = asyncio.Lock()
_rate_limit_cleanup_counter = 0

_METADATA_HANDSHAKE_TIMEOUT_SECS = 10.0


async def _rate_limit_check(client_host: str) -> None:
    global _rate_limit_cleanup_counter
    rate_limit_per_minute = int(get_ev("WS_RATE_LIMIT_PER_MINUTE", "20"))
    now = datetime.now(UTC).timestamp()
    window = 60.0
    async with _rate_limit_lock:
        timestamps = [
            t for t in _rate_limit_store.get(client_host, []) if now - t < window
        ]
        if len(timestamps) >= rate_limit_per_minute:
            raise WebSocketException(
                code=WS_1008_POLICY_VIOLATION, reason="Rate limit exceeded"
            )
        timestamps.append(now)
        _rate_limit_store[client_host] = timestamps

        _rate_limit_cleanup_counter += 1
        if _rate_limit_cleanup_counter >= 100:
            _rate_limit_cleanup_counter = 0
            cutoff = now - window
            stale_keys = [
                k for k, v in _rate_limit_store.items() if all(t < cutoff for t in v)
            ]
            for k in stale_keys:
                del _rate_limit_store[k]


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in ("true", "1", "yes")


def _parse_metadata(raw: str) -> dict | None:
    try:
        metadata = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(metadata, dict):
        return None
    return metadata


async def _receive_metadata(websocket: WebSocket) -> dict | None:
    message = await websocket.receive()
    if message.get("type") == "websocket.disconnect":
        return None
    raw = message.get("text")
    if raw is None:
        return None
    return _parse_metadata(raw)


async def _receive_metadata_with_timeout(websocket: WebSocket) -> dict | None:
    try:
        return await asyncio.wait_for(
            _receive_metadata(websocket),
            timeout=_METADATA_HANDSHAKE_TIMEOUT_SECS,
        )
    except TimeoutError:
        logger.warning("WebSocket metadata handshake timed out")
        return None


def _metadata_str(metadata: dict, key: str, default: str = "") -> str:
    value = metadata.get(key, default)
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def generate_call_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


class SilenceMixer(BaseAudioMixer):
    """Passthrough audio mixer that maintains a continuous WebSocket audio stream.

    The FastAPIWebsocketTransport calls ``mix()`` on every audio output cycle,
    including cycles where the bot is silent (listening or processing).  Without
    a mixer the transport only emits frames when TTS audio is present, so the
    client receives nothing during silence.  Many WebSocket audio clients
    (browsers, the Python test client) expect a steady byte stream and will
    stall, mis-time playback, or drop the connection if the stream goes quiet.

    This mixer solves the problem with the simplest possible implementation:
    pass every audio buffer through unchanged.  No actual mixing is required
    because only one audio source (TTS) is in play; the mixer is registered
    purely to opt in to the continuous-output behaviour of the transport.
    """

    async def start(self, sample_rate: int):
        pass

    async def stop(self):
        pass

    async def process_frame(self, frame):
        pass

    async def mix(self, audio: bytes) -> bytes:
        return audio


def _get_user_idle_timeout_secs(
    call_id: str, metadata: dict[str, object]
) -> float | None:
    raw_timeout = metadata.get("idle_timeout_secs")
    if raw_timeout is not None:
        if not isinstance(raw_timeout, str):
            raw_timeout = str(raw_timeout)
        if raw_timeout.strip():
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

    raw_timeout = get_ev("WEBSOCKET_USER_IDLE_TIMEOUT_SECS", "").strip()
    if raw_timeout == "" and call_id.startswith("ws-test"):
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


_ALLOWED_ORIGINS = [
    "http://127.0.0.1:8765",
    "http://localhost:8765",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:4173",
    "http://localhost:4173",
]


def create_app(_env: str | None = None) -> FastAPI:
    env = _env or get_ev("ENV", "development")

    if env == "production":
        raise RuntimeError(
            "The local FastAPI server must not be used in production. "
            "Deploy bot.py via Pipecat Cloud or your own runner instead."
        )

    app = FastAPI()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ALLOWED_ORIGINS,
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

        client_host = websocket.client.host if websocket.client else "unknown"
        await _rate_limit_check(client_host)

        # First-message JSON metadata handshake
        metadata = await _receive_metadata_with_timeout(websocket)
        if metadata is None:
            await websocket.close(code=4002, reason="Invalid JSON metadata")
            return

        # Optional auth token check against metadata
        ws_auth_token = get_ev("WS_AUTH_TOKEN", "")
        if ws_auth_token:
            token = _metadata_str(metadata, "auth_token")
            if token != ws_auth_token:
                await websocket.close(code=4001, reason="Invalid auth token")
                return

        caller_phone_number = _metadata_str(metadata, "caller_phone_number")
        call_id = _metadata_str(metadata, "call_id").strip() or generate_call_id()
        strict_user_muting = _parse_bool(
            _metadata_str(metadata, "strict_user_muting", "false")
        )

        user_idle_timeout_secs = _get_user_idle_timeout_secs(call_id, metadata)

        transport = FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                add_wav_header=False,
                serializer=ProtobufFrameSerializer(),
                audio_out_mixer=SilenceMixer(),
            ),
        )

        async def configure_websocket_transport(transport, task, flow_manager, call_id):
            @transport.event_handler("on_client_connected")
            async def on_client_connected(transport, client):
                with logger.contextualize(call_id=call_id):
                    logger.info(f"""WebSocket client connected for call {call_id}""")
                schedule_flow_initialization(flow_manager, node_start(), call_id)

        await run_bot(
            transport=transport,
            call_id=call_id,
            caller_phone_number=caller_phone_number,
            handle_sigint=False,
            configure_transport=configure_websocket_transport,
            user_idle_timeout_secs=user_idle_timeout_secs,
            strict_user_muting=strict_user_muting,
        )

    return app


app = create_app()
