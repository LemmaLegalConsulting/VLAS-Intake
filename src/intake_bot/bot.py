#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import datetime
import io
import os
import wave

import aiofiles
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    EndFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.google.tts import GoogleTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.stt import OpenAISTTService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat_flows import FlowManager

from intake_bot.env_var import env_var_is_true, get_env_var, require_env_var
from intake_bot.intake_nodes import node_initial
from intake_bot.intake_utils import log_flow_manager_state
from intake_bot.local_smart_turn import turn_analyzer
from intake_bot.security import verify_websocket_auth_code


async def save_audio(audio: bytes, sample_rate: int, num_channels: int):
    if len(audio) > 0:
        recordings_dir = "recordings"
        os.makedirs(recordings_dir, exist_ok=True)  # Ensure the folder exists
        filename = os.path.join(
            recordings_dir, f"recording_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
        )
        with io.BytesIO() as buffer:
            with wave.open(buffer, "wb") as wf:
                wf.setsampwidth(2)
                wf.setnchannels(num_channels)
                wf.setframerate(sample_rate)
                wf.writeframes(audio)
            async with aiofiles.open(filename, "wb") as file:
                await file.write(buffer.getvalue())
        logger.info(f"Merged audio saved to {filename}")
    else:
        logger.info("No audio data to save")


async def run_bot(transport: BaseTransport, call_data: dict, handle_sigint: bool):
    """
    Main function to set up and run the VLAS intake bot.
    """
    stt = OpenAISTTService(
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-transcribe",
        prompt="Expect words related law, legal situations, and information about people.",
    )

    llm = OpenAILLMService(api_key=require_env_var("OPENAI_API_KEY"), model="gpt-4o")

    tts = GoogleTTSService(
        credentials=require_env_var("GOOGLE_ACCESS_CREDENTIALS"),
        voice_id="en-US-Chirp3-HD-Achernar",
        push_silence_after_stop=False,
        params=GoogleTTSService.InputParams(
            language=Language.EN, gender="female", google_style="empathetic"
        ),
    )

    context = LLMContext()
    context_aggregator = LLMContextAggregatorPair(context)

    # NOTE: Watch out! This will save all the conversation in memory. You can
    # pass `buffer_size` to get periodic callbacks.
    audiobuffer = AudioBufferProcessor()

    pipeline = Pipeline(
        [
            transport.input(),  # Websocket input from client
            stt,  # Speech-To-Text
            context_aggregator.user(),
            llm,  # LLM
            tts,  # Text-To-Speech
            transport.output(),  # Websocket output to client
            audiobuffer,  # Used to buffer the audio in the pipeline
            context_aggregator.assistant(),
        ]
    )

    observers = list()

    if env_var_is_true("ENABLE_WHISKER"):
        from pipecat_whisker import WhiskerObserver

        whisker = WhiskerObserver(pipeline)
        observers.append(whisker)

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=observers,
    )

    # Initialize flow manager with LLM
    flow_manager = FlowManager(
        task=task,
        llm=llm,
        context_aggregator=context_aggregator,
    )

    # Add flow manager state from Twilio's call_data
    flow_manager.state["phone"] = call_data["body"].get("caller_phone_number")

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        # Start recording.
        await audiobuffer.start_recording()
        # Kick off the conversation.
        await flow_manager.initialize(node_initial())

    @transport.event_handler("on_session_timeout")
    async def handle_timeout(transport, websocket):
        # Play timeout message before ending call
        logger.info("Call timed out; ending.")
        await task.queue_frames(
            [
                TTSSpeakFrame(
                    "Thank you for calling Virginia's Law-Line Legal Help Service. It seems that you have disconnected. Please feel free to call us back. Goodbye!"
                ),
                EndFrame(),
            ]
        )

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        await task.cancel()

    @task.event_handler("on_pipeline_finished")
    async def on_pipeline_finished(task, frame):
        log_flow_manager_state(flow_manager)

    @audiobuffer.event_handler("on_audio_data")
    async def on_audio_data(buffer, audio, sample_rate, num_channels):
        if env_var_is_true("SAVE_AUDIO_RECORDINGS"):
            await save_audio(audio, sample_rate, num_channels)

    # We use `handle_sigint=False` because `uvicorn` (not sure if this
    # applies since we're using `granian` now) is controlling keyboard
    # interruptions. We use `force_gc=True` to force garbage collection
    # after the runner finishes running a task which could be useful for
    # long running applications with multiple clients connecting.

    if env_var_is_true("ENABLE_TAIL"):
        from pipecat_tail.runner import TailRunner

        runner = TailRunner(handle_sigint=handle_sigint, force_gc=True)
        await runner.run(task)
    else:
        runner = PipelineRunner(handle_sigint=handle_sigint, force_gc=True)
        await runner.run(task)


async def bot(runner_args: RunnerArguments) -> None | dict[str, int]:
    """
    Main bot entry point.
    """
    transport_type, call_data = await parse_telephony_websocket(runner_args.websocket)
    logger.info(f"Auto-detected transport: {transport_type}")

    call_id = call_data["call_id"]

    if env_var_is_true("TEST_CLIENT_ALLOWED"):
        call_data["body"]["caller_phone_number"] = "8665345243"
        call_is_valid = True
    else:
        websocket_auth_code = call_data["body"].get("websocket_auth_code")
        call_is_valid = verify_websocket_auth_code(
            call_id=call_id,
            received_code=websocket_auth_code,
        )

    if not call_is_valid:
        logger.debug(f"""WebSocket connection denied for call_id: {call_id}""")
        return {"code": 1008}

    logger.debug(f"""WebSocket connection accepted for call_id: {call_id}""")

    serializer = TwilioFrameSerializer(
        stream_sid=call_data["stream_id"],
        call_sid=call_data["call_id"],
        account_sid=require_env_var("TWILIO_ACCOUNT_SID"),
        auth_token=require_env_var("TWILIO_AUTH_TOKEN"),
    )

    transport = FastAPIWebsocketTransport(
        websocket=runner_args.websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer,
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(stop_secs=float(get_env_var("VAD_STOP_SECS", 0.2)))
            ),
            turn_analyzer=turn_analyzer,
        ),
    )

    handle_sigint = runner_args.handle_sigint

    await run_bot(transport, call_data, handle_sigint)
