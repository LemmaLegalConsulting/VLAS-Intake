"""Single-process demo host. No database, telephone service, or case submission."""
import asyncio
import contextlib
import os
import logging
import secrets
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles
from demo.explain import catalog, explanations

MAX_SESSIONS = 2
SESSION_SECONDS = 600
RESULT_TTL = 300
STARTS_PER_HOUR = 30
FIELDS = {
    "language": "Language", "names": "Name", "phone": "Phone number",
    "service_area": "Location", "case_type": "Legal issue",
    "adverse_parties": "Other parties", "domestic_violence": "Safety",
    "household_composition": "Household", "income": "Income", "assets": "Assets",
    "citizenship": "Citizenship", "address": "Address",
}


@dataclass
class Session:
    created: float = field(default_factory=time.monotonic)
    started: bool = False
    finished: float | None = None
    state: dict = field(default_factory=dict)
    error: str | None = None


def summary(session):
    return {
        "finished": session.finished is not None,
        "error": session.error,
        "explanations": explanations(session.state),
        "sections": [
            {"title": label, "data": jsonable_encoder(session.state[key])}
            for key, label in FIELDS.items() if session.state.get(key)
        ],
    }


def load_voice_runtime():
    # Imports can take several seconds after a Machine wakes. Keep them off the
    # event loop so WebSocket handshakes and health checks remain responsive.
    from loguru import logger

    logger.remove()
    logger.add(lambda message: None)
    from intake_bot.bot import run_bot
    from intake_bot.nodes import nodes
    from pipecat.audio.mixers.base_audio_mixer import BaseAudioMixer
    from pipecat.serializers.protobuf import ProtobufFrameSerializer
    from pipecat.transports.websocket.fastapi import (
        FastAPIWebsocketParams, FastAPIWebsocketTransport,
    )

    async def node_initial(flow):
        # The browser has no caller ID. Complete this system-only bootstrap
        # deterministically so the LLM starts at the actual language question.
        _, language_node = await nodes.system_phone_number(flow)
        return nodes.node_partial_reset_with_summary() | language_node

    return (run_bot, node_initial, BaseAudioMixer, ProtobufFrameSerializer,
            FastAPIWebsocketParams, FastAPIWebsocketTransport)


async def execute_session(websocket, session):
    # Keep imports lazy: health checks and static pages don't initialize voice models.
    (run_bot, node_initial, BaseAudioMixer, ProtobufFrameSerializer,
     FastAPIWebsocketParams, FastAPIWebsocketTransport) = await asyncio.to_thread(load_voice_runtime)

    class SilenceMixer(BaseAudioMixer):
        async def start(self, sample_rate):
            pass

        async def stop(self):
            pass

        async def process_frame(self, frame):
            pass

        async def mix(self, audio):
            return audio

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True, audio_out_enabled=True, add_wav_header=False,
            serializer=ProtobufFrameSerializer(), audio_out_mixer=SilenceMixer(),
        ),
    )

    async def configure(transport, task, flow, call_id):
        session.state = flow.state

        @transport.event_handler("on_client_connected")
        async def connected(transport, client):
            initial = await node_initial(flow)
            # Install the intake context before speaking. A blocking TTS
            # pre-action can be interrupted before its completion frame arrives,
            # leaving the flow uninitialized while user speech reaches the LLM.
            initial.pop("pre_actions", None)
            # Preserve the actual flow, but make its demonstration status explicit.
            initial.setdefault("task_messages", []).append({
                "role": "system",
                "content": "This is a Lemma Legal technology demo using a sample Virginia legal aid intake. "
                "Start with the exact bilingual language-selection question in the instructions. "
                "The page tells the visitor to use invented details. No application will be submitted and no texts will be sent. "
                "They can finish at any point to see the information collected so far.",
            })
            await flow.initialize(initial)

    await run_bot(transport, secrets.token_hex(12), "", False,
                  configure_transport=configure, user_idle_timeout_secs=20,
                  demo_mode=True, audio_out_sample_rate=24000, audio_in_sample_rate=16000)


def create_app(runner=execute_session):
    sessions: dict[str, Session] = {}
    starts: deque[float] = deque()
    allowed = set(os.getenv("DEMO_ALLOWED_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080").split(","))

    def prune():
        now = time.monotonic()
        for token, session in list(sessions.items()):
            if ((session.finished is not None and now - session.finished > RESULT_TTL)
                    or (not session.started and now - session.created > 60)):
                del sessions[token]
        while starts and now - starts[0] > 3600:
            starts.popleft()

    @asynccontextmanager
    async def lifespan(app):
        async def cleanup():
            while True:
                await asyncio.sleep(30)
                prune()
        job = asyncio.create_task(cleanup())
        try:
            yield
        finally:
            job.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await job
            sessions.clear()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' blob:; style-src 'self'; "
            "connect-src 'self'; media-src 'self' blob:; worker-src 'self' blob:; "
            "frame-ancestors 'self' https://lemmalegal.com https://www.lemmalegal.com"
        )
        return response

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/api/demo")
    async def demo_info():
        return catalog()

    @app.post("/api/sessions")
    async def create(request: Request):
        if request.headers.get("origin") not in allowed:
            raise HTTPException(403, "This site is not allowed to start a demo.")
        prune()
        if len(starts) >= STARTS_PER_HOUR:
            raise HTTPException(429, "The demo has reached its hourly limit. Please try later.")
        if sum(s.finished is None for s in sessions.values()) >= MAX_SESSIONS:
            raise HTTPException(429, "The demo is busy. Please try again in a minute.")
        if runner is execute_session and not all(os.getenv(key) for key in (
            "AZURE_API_KEY", "AZURE_LLM_ENDPOINT", "AZURE_LLM_MODEL",
            "AZURE_SPEECH_REGION", "AZURE_SPEECH_VOICE",
        )):
            raise HTTPException(503, "Voice service is not configured yet.")
        token = secrets.token_urlsafe(32)
        sessions[token] = Session()
        starts.append(time.monotonic())
        return {"token": token, "max_seconds": SESSION_SECONDS}

    @app.get("/api/results")
    async def results(request: Request):
        prune()
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if token not in sessions:
            raise HTTPException(404, "This demo session has expired. Please start again.")
        return summary(sessions[token])

    @app.websocket("/ws/{token}")
    async def voice(websocket: WebSocket, token: str):
        prune()
        session = sessions.get(token)
        if (websocket.headers.get("origin") not in allowed or not session
                or session.started or session.finished is not None):
            await websocket.close(code=1008)
            return
        session.started = True
        await websocket.accept()
        try:
            async with asyncio.timeout(SESSION_SECONDS) as deadline:
                await runner(websocket, session)
            if deadline.expired():
                session.error = "The ten-minute demo has ended. Here is what was collected."
        except TimeoutError:
            session.error = "The ten-minute demo has ended. Here is what was collected."
        except Exception as exc:
            logging.getLogger("demo").warning("Voice session failed (%s)", type(exc).__name__)
            session.error = "The voice connection ended unexpectedly. Your partial results are below."
        finally:
            session.finished = time.monotonic()
            with contextlib.suppress(Exception):
                await websocket.close()

    app.mount("/", StaticFiles(directory=Path(__file__).parent / "web" / "dist", html=True, check_dir=False))
    return app


app = create_app()
