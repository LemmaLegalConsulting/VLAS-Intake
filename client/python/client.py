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

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import EndFrame, OutputTransportMessageUrgentFrame
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


async def run_client(client_name: str, websocket_url: str, duration_secs: int):
    stream_sid = str(uuid4())
    call_sid = str(uuid4())

    transport = WebsocketClientTransport(
        uri=websocket_url,
        params=WebsocketClientParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=TwilioFrameSerializer(
                stream_sid=stream_sid,
                call_sid=call_sid,
                params=TwilioFrameSerializer.InputParams(auto_hang_up=False),
            ),
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(stop_secs=float(os.getenv("VAD_STOP_SECS")))
            ),
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

    messages = [
        {
            "role": "system",
            "content": "This conversation is being converted to voice. You are an adult woman, calling a legal aid service, seeking help with your divorce. You will need to answer questions to complete your legal aid intake. Wait until you are asked a question, then respond with the appropriate information. The following is the information about you that you will use to answer the questions: Your phone number is (866) 534-5243. Your name is Celeste Caroline Campbell. You live in Amelia County. Your husband's name is Dexter Robert Campbell. You don't have any income because you're a stay at home mom. You don't have any of your own assets since he makes all of the money. Your husband has yelled at you and thrown things, but never hit you or the kids. You are a US citizen. This isn't an emergency, but you'd like to get out of the house and start the divorce as soon as possible.",
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
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    @transport.event_handler("on_connected")
    async def on_connected(transport: WebsocketClientTransport, client):
        # Start recording.
        await audiobuffer.start_recording()

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
                    duration_secs=args.duration,
                )
            )
        )
    await asyncio.gather(*clients)


if __name__ == "__main__":
    asyncio.run(main())
