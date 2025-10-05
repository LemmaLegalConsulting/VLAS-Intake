#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import argparse
import asyncio
import os
import sys
from uuid import uuid4

import yaml
from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import EndFrame, LLMRunFrame, OutputTransportMessageUrgentFrame
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

load_dotenv(override=True)

logger.remove(0)
logger.add(sys.stderr, level="DEBUG")


DEFAULT_CLIENT_DURATION = 600

if os.getenv("LOCAL_SMART_TURN") == "TRUE":
    try:
        from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3

        turn_analyzer = LocalSmartTurnAnalyzerV3()
    except Exception:
        sys.exit(
            "[INFO] intake-bot: You can also use `uv sync --group lst` to install the modules for LocalSmartTurnAnalyzerV3"
        )
else:
    logger.info("LocalSmartTurnAnalyzerV3 is not enabled.")
    turn_analyzer = None


async def run_client(client_name: str, websocket_url: str, script: str, duration_secs: int):
    stream_sid = str(uuid4())
    call_sid = str(uuid4())

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

    stt = OpenAISTTService(
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-transcribe",
        prompt="Expect words related law, legal situations, and information about people.",
    )

    llm = OpenAILLMService(api_key=os.getenv("OPENAI_API_KEY"), model="gpt-4o")

    tts = GoogleTTSService(
        credentials=os.getenv("GOOGLE_ACCESS_CREDENTIALS"),
        voice_id="en-US-Chirp3-HD-Despina",
        push_silence_after_stop=False,
        params=GoogleTTSService.InputParams(
            language=Language.EN, gender="female", google_style="empathetic"
        ),
    )

    with open("scripts.yml") as f:
        scripts: dict = yaml.safe_load(f)

    messages = [
        {
            "role": "system",
            "content": scripts[script],
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
                "start": {"streamSid": stream_sid, "callSid": call_sid},
            }
        )
        await transport.output().send_message(message)

        # Kick off the conversation.
        messages.append(
            {
                "role": "system",
                "content": "You may begin with 'Thank you for taking my call', but otherwise please wait to be asked a question before responding.",
            }
        )
        await task.queue_frames([LLMRunFrame()])

    async def end_call():
        await asyncio.sleep(duration_secs)
        logger.info(f"Client {client_name} finished after {duration_secs} seconds.")
        await task.queue_frame(EndFrame())

    runner = PipelineRunner()

    await asyncio.gather(runner.run(task), end_call())


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
        default="celeste",
        help="specify the script to use from `scripts.yml`",
    )
    parser.add_argument(
        "-d",
        "--duration",
        type=int,
        default=DEFAULT_CLIENT_DURATION,
        help=f"duration of each client in seconds (default: {DEFAULT_CLIENT_DURATION})",
    )
    args, _ = parser.parse_known_args()

    clients = []
    for i in range(args.clients):
        clients.append(
            asyncio.create_task(
                run_client(
                    client_name=f"client_{i}",
                    websocket_url=args.url,
                    script=args.script,
                    duration_secs=args.duration,
                )
            )
        )
    await asyncio.gather(*clients)


if __name__ == "__main__":
    asyncio.run(main())
