import argparse
import asyncio
import io
import os
import random
import sys
import wave
from pathlib import Path
from uuid import uuid4

import yaml
from dotenv import load_dotenv
from loguru import logger
from openai import AsyncOpenAI
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    LLMMessagesUpdateFrame,
    LLMRunFrame,
    OutputTransportMessageUrgentFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.google.tts import GoogleTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.stt import OpenAISTTService
from pipecat.transcriptions.language import Language
from pipecat.transports.websocket.client import (
    WebsocketClientParams,
    WebsocketClientTransport,
)
from test_manager import TestRunner

# Add src to path so we can import from intake_bot
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))
from intake_bot.utils.security import generate_websocket_auth_code


def _patch_pipecat_websocket_client_double_connect() -> None:
    """Avoid a race that can create two WS connections.

    In pipecat, both WebsocketClientInputTransport.start() and
    WebsocketClientOutputTransport.start() call WebsocketClientSession.connect().
    Those starts can run concurrently, and connect() isn't guarded against
    concurrent entry (it checks self._websocket before awaiting connect).

    Result: two near-simultaneous websocket_connect() calls, creating two
    separate server connections for a single logical call.
    """

    try:
        from pipecat.transports.websocket.client import WebsocketClientSession
    except Exception:
        return

    if getattr(WebsocketClientSession, "_vlas_connect_patch", False):
        return

    original_connect = WebsocketClientSession.connect

    async def connect_with_lock(self):
        lock = getattr(self, "_vlas_connect_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            setattr(self, "_vlas_connect_lock", lock)
        async with lock:
            return await original_connect(self)

    WebsocketClientSession.connect = connect_with_lock  # type: ignore[method-assign]
    WebsocketClientSession._vlas_connect_patch = True  # type: ignore[attr-defined]


_patch_pipecat_websocket_client_double_connect()


def _wav_duration_seconds(wav_bytes: bytes) -> float:
    if not wav_bytes:
        return 0.0
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate() or 0
        return (frames / rate) if rate else 0.0
    except Exception:
        return 0.0


class OpenAISTTServiceMinDuration(OpenAISTTService):
    """Prevents OpenAI STT calls for too-short segments.

    OpenAI's transcription endpoint rejects very short audio (e.g. <0.1s).
    Segmented STT can occasionally emit an empty/near-empty segment when VAD
    triggers start/stop without enough audio buffered.
    """

    def __init__(self, *args, min_duration_seconds: float = 0.1, **kwargs):
        super().__init__(*args, **kwargs)
        self._min_duration_seconds = float(min_duration_seconds)

    async def run_stt(self, audio: bytes):
        duration = _wav_duration_seconds(audio)
        if duration < self._min_duration_seconds:
            logger.debug(
                f"Skipping STT: segment duration {duration:.3f}s < {self._min_duration_seconds:.3f}s"
            )
            return

        async for frame in super().run_stt(audio):
            yield frame


load_dotenv(override=True)

# Global variable to track call IDs for testing
call_id_map = {}

logger.remove(0)
logger.add(sys.stderr, level="DEBUG")


# Load scripts.yml from the same directory as this script
script_dir = Path(__file__).parent
scripts_file = script_dir / "scripts.yml"

with open(scripts_file) as f:
    scripts: dict = yaml.safe_load(f)


if os.getenv("DISABLE_LOCAL_SMART_TURN") == "TRUE":
    logger.info("LocalSmartTurnAnalyzerV3 is not enabled.")
    turn_analyzer = None
else:
    try:
        from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3

        turn_analyzer = LocalSmartTurnAnalyzerV3()
    except Exception:
        sys.exit(
            "[INFO] intake-bot: You are missing the module 'LocalSmartTurnAnalyzerV3'. You use `uv sync` to install the module."
        )


async def run_client(
    client_name: str,
    websocket_url: str,
    script: str,
    phone_number: str,
    validate_state: bool = False,
):
    sid = str(uuid4())
    stream_sid = sid
    call_sid = sid

    auth_code = generate_websocket_auth_code(call_sid)

    # Track this call for later validation
    call_id_map[client_name] = {"stream_sid": stream_sid, "call_sid": call_sid, "script": script}
    logger.info(call_id_map[client_name])

    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        account_sid=os.getenv("TWILIO_ACCOUNT_SID"),
        auth_token=os.getenv("TWILIO_AUTH_TOKEN"),
    )

    transport = WebsocketClientTransport(
        uri=websocket_url,
        params=WebsocketClientParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer,
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(stop_secs=float(os.getenv("VAD_STOP_SECS", 2.0)))
            ),
            turn_analyzer=turn_analyzer,
        ),
    )

    stt = OpenAISTTServiceMinDuration(
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-mini-transcribe",
        prompt="Expect words related law, legal situations, and information about people.",
        min_duration_seconds=0.1,
    )

    llm = OpenAILLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-mini",
    )

    tts = GoogleTTSService(
        credentials=os.getenv("GOOGLE_ACCESS_CREDENTIALS"),
        voice_id="en-US-Chirp3-HD-Despina",
        push_silence_after_stop=False,
        params=GoogleTTSService.InputParams(
            language=Language.EN, gender="female", google_style="empathetic"
        ),
    )

    messages = [
        {
            "role": "system",
            "content": scripts[script]["system_prompt"],
        },
    ]

    context = OpenAILLMContext(messages)
    context_aggregator = llm.create_context_aggregator(context)

    # NOTE: Watch out! This will save all the conversation in memory. You can
    # pass `buffer_size` to get periodic callbacks.
    audiobuffer = AudioBufferProcessor()

    pipeline = Pipeline(
        [
            transport.input(),  # Websocket input from server
            stt,  # Speech-To-Text
            context_aggregator.user(),
            llm,  # LLM
            tts,  # Text-To-Speech
            transport.output(),  # Websocket output to server
            audiobuffer,  # Used to buffer the audio in the pipeline
            context_aggregator.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    @transport.event_handler("on_connected")
    async def on_connected(transport: WebsocketClientTransport, client):
        message = OutputTransportMessageUrgentFrame(
            message={"event": "connected", "protocol": "Call", "version": "1.0.0"}
        )
        await transport.output().send_message(message)

        message = OutputTransportMessageUrgentFrame(
            message={
                "event": "start",
                "streamSid": stream_sid,
                "callSid": call_sid,
                "start": {
                    "streamSid": stream_sid,
                    "callSid": call_sid,
                    "customParameters": {
                        "websocket_auth_code": auth_code,
                        "caller_phone_number": phone_number,
                    },
                },
            }
        )
        await transport.output().send_message(message)

        # Kick off the conversation.
        messages.append(
            {
                "role": "system",
                "content": "Please wait to be asked a question before responding.",
            }
        )
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_disconnected")
    async def on_disconnected(transport: WebsocketClientTransport, client):
        logger.info(f"Client {client_name} disconnected from server")
        await task.cancel()

    async def periodic_summarizer():
        summ_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        while True:
            await asyncio.sleep(120)
            try:
                current_messages = context.messages
                if len(current_messages) < 10:
                    continue

                logger.debug(f"Client {client_name} summarizing conversation...")
                conversation = [m for m in current_messages if m["role"] != "system"]

                if not conversation:
                    continue

                response = await summ_client.chat.completions.create(
                    model="gpt-4o-mini",
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
                    {"role": "system", "content": scripts[script]["system_prompt"]},
                    {
                        "role": "system",
                        "content": f"Previous conversation summary: {summary}",
                    },
                ]

                await task.queue_frame(LLMMessagesUpdateFrame(messages=new_messages))
                logger.info(f"Client {client_name} context updated with summary.")
            except Exception as e:
                logger.error(f"Client {client_name} summarization failed: {e}")

    runner = PipelineRunner(handle_sigint=True)

    summarizer_task = asyncio.create_task(periodic_summarizer())

    try:
        # Run the pipeline and wait for it to complete
        # The server controls when the call ends via on_session_timeout
        await runner.run(task)
    except asyncio.CancelledError:
        logger.debug(f"Client {client_name} task was cancelled")
    except Exception as e:
        logger.error(
            f"Client {client_name} pipeline error: {type(e).__name__}: {e}",
            exc_info=True,
        )
    finally:
        summarizer_task.cancel()
        # Ensure task is cancelled
        if not task._cancelled:
            await task.cancel()

        # Wait for server to save its results
        await asyncio.sleep(1.0)

        # Validate state if requested
        if validate_state:
            script_config = scripts.get(script, {})
            if isinstance(script_config, dict) and "expected_state" in script_config:
                expected_state = script_config["expected_state"]
                # Look for logs/flow_manager_state.json in the intake-bot root
                flow_manager_state_file = (
                    Path(__file__).parent.parent.parent / "logs" / "flow_manager_state.json"
                )
                # Store client test results in logs/client_test_results.json
                client_test_results_file = (
                    Path(__file__).parent.parent.parent / "logs" / "client_test_results.json"
                )
                # Use TestRunner for validation
                runner = TestRunner(
                    results_file=str(client_test_results_file),
                    flow_manager_state_file=str(flow_manager_state_file),
                )
                passed, mismatches = await runner.validate_call(call_sid, script, expected_state)
                if passed:
                    logger.info(f"Client {client_name} state validation PASSED")
                else:
                    logger.warning(
                        f"Client {client_name} state validation FAILED with {len(mismatches)} mismatches"
                    )
                    for mismatch in mismatches[:5]:  # Log first 5 mismatches
                        logger.warning(f"  - {mismatch}")
            else:
                logger.info(f"No expected_state defined for script '{script}', skipping validation")


async def main():
    parser = argparse.ArgumentParser(description="Pipecat Twilio Chatbot Client")
    parser.add_argument(
        "-u",
        "--url",
        type=str,
        default="ws://localhost:8765/ws",
        help="specify the websocket URL",
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
    args, _ = parser.parse_known_args()

    # Validate script argument if provided
    if args.script is None:
        args.script = random.choice(list(scripts.keys()))
    elif args.script not in scripts.keys():
        parser.error(
            f"""Script '{args.script}' not found in scripts.yml. Available scripts: {", ".join(scripts.keys())}"""
        )
    logger.info(f"""Using script: '{args.script}'""")
    if args.validate:
        logger.info("State validation enabled for all calls")

    clients = []
    for i in range(args.clients):
        clients.append(
            asyncio.create_task(
                run_client(
                    client_name=f"client_{i}",
                    websocket_url=args.url,
                    script=args.script,
                    phone_number=args.phone,
                    validate_state=args.validate,
                )
            )
        )

    await asyncio.gather(*clients)


if __name__ == "__main__":
    asyncio.run(main())
