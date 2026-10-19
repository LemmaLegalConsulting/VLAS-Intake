import argparse
import asyncio
import json
import os
import random
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv
from loguru import logger
from openai import AsyncAzureOpenAI
from pipecat.audio.mixers.base_audio_mixer import BaseAudioMixer
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    LLMMessagesUpdateFrame,
    MixerControlFrame,
    TranscriptionFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.services.azure.llm import AzureLLMService
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.deepgram.flux.tts import DeepgramFluxTTSService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.transcriptions.language import Language
from pipecat.transports.websocket.client import (
    WebsocketClientParams,
    WebsocketClientTransport,
)
from pipecat.turns.user_start.external_user_turn_start_strategy import (
    ExternalUserTurnStartStrategy,
)
from pipecat.turns.user_stop.external_user_turn_stop_strategy import (
    ExternalUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner
from scenarios import build_client_system_prompt, load_scripts
from test_manager import TestRunner

sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from intake_bot.utils.ev import get_deepgram_tts_voices


def _patch_pipecat_websocket_client_double_connect() -> None:
    """Avoid a race where input and output open separate websockets."""

    try:
        from pipecat.transports.websocket.client import WebsocketClientSession
    except Exception:  # noqa: BLE001 - patch is optional across Pipecat versions
        return

    if getattr(WebsocketClientSession, "_vlas_connect_patch", False):
        return

    original_connect = WebsocketClientSession.connect

    async def connect_with_lock(self):
        lock = getattr(self, "_vlas_connect_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._vlas_connect_lock = lock
        async with lock:
            return await original_connect(self)

    WebsocketClientSession.connect = connect_with_lock  # type: ignore[method-assign]
    WebsocketClientSession._vlas_connect_patch = True  # type: ignore[attr-defined]


_patch_pipecat_websocket_client_double_connect()


load_dotenv(override=True)

logger.remove()
logger.add(sys.stderr, level="DEBUG")

scripts: dict = load_scripts()


INTERIM_FINALIZE_TIMEOUT_SECS = 1.5


class InterimTranscriptionFinalizer(FrameProcessor):
    """Promotes interim STT transcriptions to finals after a quiet period.

    The server transport has no audio mixer, so it only sends audio frames
    during TTS playback.  Between utterances the client's Deepgram Flux STT receives
    no audio and therefore never produces a 'Recognized' (final) event —
    only 'Recognizing' (interim) events.  This processor watches for interim
    frames and, if no new interim arrives within *timeout* seconds, promotes
    the last one to a full TranscriptionFrame so the LLM aggregator can
    process it.
    """

    def __init__(self, timeout: float = INTERIM_FINALIZE_TIMEOUT_SECS):
        super().__init__()
        self._timeout = timeout
        self._last_interim: InterimTranscriptionFrame | None = None
        self._timer_task: asyncio.Task | None = None

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            # A real final arrived — cancel any pending promotion.
            self._cancel_timer()
            self._last_interim = None
            await self.push_frame(frame, direction)
        elif isinstance(frame, InterimTranscriptionFrame) and frame.text.strip():
            self._last_interim = frame
            self._restart_timer()
            await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)

    def _restart_timer(self):
        self._cancel_timer()
        self._timer_task = asyncio.create_task(self._finalize_after_timeout())

    def _cancel_timer(self):
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
        self._timer_task = None

    async def _finalize_after_timeout(self):
        await asyncio.sleep(self._timeout)
        interim = self._last_interim
        if interim is not None:
            self._last_interim = None
            final = TranscriptionFrame(
                text=interim.text,
                user_id=interim.user_id,
                timestamp=interim.timestamp,
                language=interim.language,
            )
            await self.push_frame(final)


class SilenceMixer(BaseAudioMixer):
    """No-op mixer that enables continuous audio output from the transport.

    When an audio_out_mixer is set on the transport, the output loop sends
    audio frames continuously — TTS audio when speaking, silence when idle.
    Without a mixer, the transport only sends frames during TTS playback,
    leaving the remote side's Flux STT starved of audio and unable to
    finalize recognitions.
    """

    async def start(self, sample_rate: int):
        pass

    async def stop(self):
        pass

    async def process_frame(self, frame: MixerControlFrame):
        pass

    async def mix(self, audio: bytes) -> bytes:
        return audio


def _get_env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def _require_env(*names: str) -> str:
    value = _get_env(*names)
    if value is None:
        joined_names = ", ".join(names)
        raise ValueError(
            f"""One of these environment variables must be set: {joined_names}"""
        )
    return value


def _build_websocket_metadata(
    call_id: str,
    phone_number: str,
    idle_timeout_secs: float | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "call_id": call_id,
        "caller_phone_number": phone_number,
    }
    auth_token = _get_env("WS_AUTH_TOKEN")
    if auth_token is not None:
        metadata["auth_token"] = auth_token
    if idle_timeout_secs is not None:
        metadata["idle_timeout_secs"] = idle_timeout_secs
    return metadata


def _build_websocket_url(server_url: str) -> str:
    base_url = server_url.rstrip("/")
    if base_url.startswith("http://"):
        base_url = "ws://" + base_url[len("http://") :]
    elif base_url.startswith("https://"):
        base_url = "wss://" + base_url[len("https://") :]

    if not base_url.endswith("/ws"):
        base_url = f"""{base_url}/ws"""

    return base_url


def _new_call_id(client_name: str, prefix: str = "ws-test") -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    normalized_client_name = client_name.replace(" ", "-")
    if prefix:
        return f"""{prefix}-{timestamp}-{normalized_client_name}"""
    return f"""{timestamp}-{normalized_client_name}"""


async def run_client(
    client_name: str,
    server_url: str,
    script: str,
    phone_number: str,
    call_id: str,
    validate_state: bool = False,
    server_idle_timeout_secs: float | None = None,
):
    azure_api_key = _require_env("AZURE_API_KEY")
    azure_llm_endpoint = _require_env("AZURE_LLM_ENDPOINT", "AZURE_CHATGPT_ENDPOINT")
    azure_llm_model = _require_env("AZURE_LLM_MODEL", "AZURE_CHATGPT_MODEL")
    deepgram_api_key = _require_env("DEEPGRAM_API_KEY")
    deepgram_stt_model = _get_env("DEEPGRAM_STT_MODEL", default="flux-general-multi")
    azure_summary_model = _get_env(
        "AZURE_LLM_SUMMARY_MODEL",
        "AZURE_LLM_MODEL",
        "AZURE_CHATGPT_MODEL",
    )
    azure_api_version = _get_env(
        "AZURE_LLM_API_VERSION",
        "AZURE_OPENAI_API_VERSION",
        default="2024-09-01-preview",
    )
    metadata = _build_websocket_metadata(
        call_id,
        phone_number,
        server_idle_timeout_secs,
    )

    websocket_url = _build_websocket_url(server_url)
    logger.info(f"""Client {client_name} connecting to {websocket_url}""")

    transport = WebsocketClientTransport(
        websocket_url,
        params=WebsocketClientParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=ProtobufFrameSerializer(),
            audio_out_mixer=SilenceMixer(),
        ),
    )

    # Use the script's language for client STT so Spanish callers get es-US recognition
    script_language = (
        scripts.get(script, {})
        .get("expected_state", {})
        .get("language", {})
        .get("language", "English")
    )
    client_language_hints = (
        [Language.ES, Language.EN]
        if script_language == "Spanish"
        else [Language.EN, Language.ES]
    )
    client_tts_voice = get_deepgram_tts_voices(client_language_hints[0])
    stt = DeepgramFluxSTTService(
        api_key=deepgram_api_key,
        settings=DeepgramFluxSTTService.Settings(
            model=deepgram_stt_model,
            language_hints=client_language_hints,
        ),
    )

    llm = AzureLLMService(
        api_key=azure_api_key,
        endpoint=azure_llm_endpoint,
        settings=AzureLLMService.Settings(
            model=azure_llm_model,
            temperature=0.0,
        ),
    )

    if script_language == "Spanish":
        tts = DeepgramTTSService(
            api_key=deepgram_api_key,
            settings=DeepgramTTSService.Settings(voice=client_tts_voice),
        )
    else:
        tts = DeepgramFluxTTSService(
            api_key=deepgram_api_key,
            settings=DeepgramFluxTTSService.Settings(voice=client_tts_voice),
        )

    system_prompt = build_client_system_prompt(script)
    messages = [{"role": "system", "content": system_prompt}]
    context = LLMContext(cast(Any, messages))
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                start=[ExternalUserTurnStartStrategy()],
                stop=[ExternalUserTurnStopStrategy(timeout=0.2)],
            ),
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    interim_finalizer = InterimTranscriptionFinalizer()

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            interim_finalizer,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        enable_rtvi=False,
    )

    @transport.event_handler("on_connected")
    async def on_connected(transport, client):
        logger.info(f"""Client {client_name} connected with call_id={call_id}""")
        # Send JSON metadata handshake before any protobuf frames
        await client.send(json.dumps(metadata))

    @transport.event_handler("on_disconnected")
    async def on_disconnected(transport, client):
        logger.info(f"""Client {client_name} disconnected from server""")
        await worker.cancel()

    async def periodic_summarizer():
        if not azure_summary_model:
            return

        summary_client = AsyncAzureOpenAI(
            api_key=azure_api_key,
            azure_endpoint=azure_llm_endpoint,
            api_version=azure_api_version,
        )
        while True:
            await asyncio.sleep(120)
            try:
                current_messages = context.messages
                if len(current_messages) < 10:
                    continue

                logger.debug(f"""Client {client_name} summarizing conversation...""")
                conversation = [
                    message
                    for message in current_messages
                    if isinstance(message, dict) and message.get("role") != "system"
                ]
                if not conversation:
                    continue

                response = await summary_client.chat.completions.create(
                    model=azure_summary_model,
                    messages=[
                        {
                            "role": "system",
                            "content": "Summarize the conversation so far, capturing all key information provided and the current state of the intake process.",
                        },
                        *conversation,
                    ],
                )
                summary = response.choices[0].message.content

                new_messages = [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "system",
                        "content": f"""Previous conversation summary: {summary}""",
                    },
                ]
                await worker.queue_frame(
                    LLMMessagesUpdateFrame(messages=cast(Any, new_messages))
                )
                logger.info(f"""Client {client_name} context updated with summary.""")
            except Exception as exc:  # noqa: BLE001 - summarization is best effort
                logger.error(f"""Client {client_name} summarization failed: {exc}""")

    runner = WorkerRunner(handle_sigint=True)
    await runner.add_workers(worker)
    summarizer_task = asyncio.create_task(periodic_summarizer())

    try:
        await runner.run()
    except asyncio.CancelledError:
        logger.debug(f"""Client {client_name} task was cancelled""")
    except Exception as exc:  # noqa: BLE001 - CLI pipeline boundary logs failures
        logger.error(
            f"""Client {client_name} pipeline error: {type(exc).__name__}: {exc}""",
            exc_info=True,
        )
    finally:
        summarizer_task.cancel()
        if not worker._cancelled:
            await worker.cancel()

        await asyncio.sleep(1.0)

        if validate_state:
            script_config = scripts.get(script, {})
            if isinstance(script_config, dict) and "expected_state" in script_config:
                expected_state = script_config["expected_state"]
                flow_manager_state_file = (
                    Path(__file__).parent.parent.parent
                    / "logs"
                    / "flow_manager_state.json"
                )
                client_test_results_file = (
                    Path(__file__).parent.parent.parent
                    / "logs"
                    / "client_test_results.json"
                )
                validator = TestRunner(
                    results_file=str(client_test_results_file),
                    flow_manager_state_file=str(flow_manager_state_file),
                )
                passed, mismatches = await validator.validate_call(
                    call_id,
                    script,
                    expected_state,
                )
                if passed:
                    logger.info(f"""Client {client_name} state validation PASSED""")
                else:
                    logger.warning(
                        f"""Client {client_name} state validation FAILED with {len(mismatches)} mismatches"""
                    )
                    for mismatch in mismatches[:5]:
                        logger.warning(f"""  - {mismatch}""")
            else:
                logger.info(
                    f"""No expected_state defined for script '{script}', skipping validation"""
                )


async def main():
    parser = argparse.ArgumentParser(description="Pipecat WebSocket Chatbot Client")
    parser.add_argument(
        "-u",
        "--url",
        type=str,
        default="http://localhost:8765",
        help="specify the intake-bot websocket server URL",
    )
    parser.add_argument(
        "-c",
        "--clients",
        type=int,
        default=1,
        help="number of concurrent clients",
    )
    parser.add_argument(
        "-s",
        "--script",
        type=str,
        help="specify the script to use from `scripts.yml`",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="validate state after each call completes",
    )
    parser.add_argument(
        "-p",
        "--phone",
        type=str,
        default="8665345243",
        help="caller phone number (default: 8665345243)",
    )
    parser.add_argument(
        "--call-id",
        type=str,
        help="explicit call id to use for a single test client",
    )
    parser.add_argument(
        "--call-id-prefix",
        type=str,
        default="ws-test",
        help="prefix for generated timestamp call ids (default: ws-test)",
    )
    parser.add_argument(
        "--server-idle-timeout",
        type=float,
        help="override the server-side websocket idle timeout in seconds for this test run",
    )
    args, _ = parser.parse_known_args()

    if args.call_id and args.clients != 1:
        parser.error("--call-id can only be used when --clients is 1")

    script_names = list(scripts.keys())
    if args.script is not None:
        if args.script not in scripts:
            parser.error(
                f"""Script '{args.script}' not found in scripts.yml. Available scripts: {", ".join(script_names)}"""
            )
        # Explicit script: all clients use it
        client_scripts = [args.script] * args.clients
    elif args.clients > 1:
        # No explicit script + multiple clients: round-robin across all personas
        client_scripts = [
            script_names[i % len(script_names)] for i in range(args.clients)
        ]
    else:
        client_scripts = [random.choice(script_names)]

    for cs in sorted(set(client_scripts)):
        count = client_scripts.count(cs)
        logger.info(f"""Script '{cs}': {count} client{"s" if count != 1 else ""}""")
    if args.validate:
        logger.info("State validation enabled for all calls")

    clients = [
        asyncio.create_task(
            run_client(
                client_name=f"""client_{index}""",
                server_url=args.url,
                script=client_scripts[index],
                phone_number=args.phone,
                call_id=args.call_id
                or _new_call_id(
                    client_name=f"""client_{index}""",
                    prefix=args.call_id_prefix,
                ),
                validate_state=args.validate,
                server_idle_timeout_secs=args.server_idle_timeout,
            )
        )
        for index in range(args.clients)
    ]
    await asyncio.gather(*clients)


if __name__ == "__main__":
    asyncio.run(main())
