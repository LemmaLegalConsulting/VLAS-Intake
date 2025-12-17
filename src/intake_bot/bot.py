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
    LLMMessagesAppendFrame,
    TranscriptionMessage,
    TranscriptionUpdateFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.processors.filters.stt_mute_filter import (
    STTMuteConfig,
    STTMuteFilter,
    STTMuteStrategy,
)
from pipecat.processors.transcript_processor import TranscriptProcessor
from pipecat.processors.user_idle_processor import UserIdleProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.google.tts import GoogleTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.stt import OpenAISTTService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat_flows import FlowManager

from intake_bot.nodes.nodes import caller_ended_conversation, end_conversation, node_initial
from intake_bot.nodes.utils import log_flow_manager_state, save_state_to_json
from intake_bot.services.legalserver import save_intake_legalserver
from intake_bot.utils.ev import ev_is_true, get_ev, require_ev
from intake_bot.utils.local_smart_turn import turn_analyzer
from intake_bot.utils.security import verify_websocket_auth_code


class TranscriptHandler:
    """Handles real-time transcript processing and output.

    Maintains a list of conversation messages and outputs them either to a log
    or to a file as they are received. Each message includes its timestamp and role.

    Attributes:
        messages: List of all processed transcript messages
        output_file: Optional path to file where transcript is saved. If None, outputs to log only.
    """

    def __init__(self, output_file: str | None = None):
        """Initialize handler with optional file output.

        Args:
            output_file: Path to output file. If None, outputs to log only.
        """
        self.messages: list[TranscriptionMessage] = []
        self.output_file: str | None = output_file
        logger.debug(
            f"TranscriptHandler initialized {'with output_file=' + output_file if output_file else 'with log output only'}"
        )

    async def save_transcript_message(self, message: TranscriptionMessage):
        """Save a single transcript message.

        Outputs the message to the log and optionally to a file.

        Args:
            message: The message to save
        """
        timestamp = f"[{message.timestamp}] " if message.timestamp else ""
        line = f"{timestamp}{message.role}: {message.content}"

        # Always log the message
        logger.debug(f"Transcript: {line}")

        # Optionally write to file
        if self.output_file:
            try:
                async with aiofiles.open(self.output_file, "a", encoding="utf-8") as f:
                    await f.write(line + "\n")
            except Exception as e:
                logger.error(f"Error saving transcript message to file: {e}")

    async def on_transcript_update(
        self, processor: TranscriptProcessor, frame: TranscriptionUpdateFrame
    ):
        """Handle new transcript messages.

        Args:
            processor: The TranscriptProcessor that emitted the update
            frame: TranscriptionUpdateFrame containing new messages
        """
        for msg in frame.messages:
            self.messages.append(msg)
            await self.save_transcript_message(msg)


async def bot(runner_args: RunnerArguments) -> None | dict[str, int]:
    """
    Main bot entry point.
    """
    transport_type, call_data = await parse_telephony_websocket(runner_args.websocket)
    logger.info(f"Auto-detected transport: {transport_type}")

    call_id = call_data["call_id"]
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
        account_sid=require_ev("TWILIO_ACCOUNT_SID"),
        auth_token=require_ev("TWILIO_AUTH_TOKEN"),
    )

    transport = FastAPIWebsocketTransport(
        websocket=runner_args.websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer,
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(
                    start_secs=float(get_ev("VAD_START_SECS", 0.1)),
                    stop_secs=float(get_ev("VAD_STOP_SECS", 0.5)),
                )
            ),
            turn_analyzer=turn_analyzer,
        ),
    )

    handle_sigint = runner_args.handle_sigint

    await run_bot(transport, call_data, handle_sigint)


async def run_bot(transport: BaseTransport, call_data: dict, handle_sigint: bool):
    """
    Main function to set up and run the VLAS intake bot.
    """
    stt = OpenAISTTService(
        api_key=require_ev("OPENAI_API_KEY"),
        model="gpt-4o-transcribe",
        prompt="Expect words related to law, legal situations, and information about people. The language may be English or Spanish.",
        language=None,
    )

    stt_mute_processor = STTMuteFilter(
        config=STTMuteConfig(
            strategies={
                STTMuteStrategy.MUTE_UNTIL_FIRST_BOT_COMPLETE,
                STTMuteStrategy.FUNCTION_CALL,
            }
        ),
    )

    llm = OpenAILLMService(api_key=require_ev("OPENAI_API_KEY"), model="gpt-4o")

    tts = GoogleTTSService(
        credentials=require_ev("GOOGLE_ACCESS_CREDENTIALS"),
        voice_id="en-US-Chirp3-HD-Achernar",
        push_silence_after_stop=False,
        params=GoogleTTSService.InputParams(
            language=Language.EN, gender="female", google_style="empathetic"
        ),
    )

    context = LLMContext()
    context_aggregator = LLMContextAggregatorPair(context)

    async def handle_user_idle(user_idle: UserIdleProcessor, retry_count: int) -> bool:
        if retry_count == 1:
            # First attempt: Add a gentle prompt to the conversation
            message = {
                "role": "system",
                "content": "The user has been quiet. Politely and briefly ask if they're still there.",
            }
            await user_idle.push_frame(LLMMessagesAppendFrame([message], run_llm=True))
            return True
        elif retry_count == 2:
            # Second attempt: More direct prompt
            message = {
                "role": "system",
                "content": "The user is still inactive. Ask if they'd like to continue our conversation.",
            }
            await user_idle.push_frame(LLMMessagesAppendFrame([message], run_llm=True))
            return True
        else:
            # Third attempt: End the conversation
            await user_idle.push_frame(
                TTSSpeakFrame(
                    "It seems like you're busy right now. Feel free to call back. Have a nice day!"
                )
            )
            await task.queue_frame(EndFrame())
            return False

    user_idle = UserIdleProcessor(callback=handle_user_idle, timeout=10.0)

    # Create transcript processor and handler
    transcript = TranscriptProcessor()
    transcript_file = None
    if ev_is_true("LOG_TO_FILE"):
        os.makedirs("logs", exist_ok=True)
        transcript_file = f"logs/transcript_{call_data['call_id']}.txt"
        logger.info(f"Logging transcript to file: {transcript_file}")
    transcript_handler = TranscriptHandler(output_file=transcript_file)

    # NOTE: Watch out! This will save all the conversation in memory. You can
    # pass `buffer_size` to get periodic callbacks.
    audiobuffer = AudioBufferProcessor()

    pipeline = Pipeline(
        [
            transport.input(),  # Websocket input from client
            stt,  # Speech-To-Text
            stt_mute_processor,  # STTMuteStrategy
            transcript.user(),  # User transcripts
            user_idle,  # Idle user check-in
            context_aggregator.user(),
            llm,  # LLM
            tts,  # Text-To-Speech
            transport.output(),  # Websocket output to client
            audiobuffer,  # Used to buffer the audio in the pipeline
            transcript.assistant(),  # Assistant transcripts
            context_aggregator.assistant(),
        ]
    )

    observers = list()
    if ev_is_true("ENABLE_TAIL_OBSERVER"):
        from pipecat_tail.observer import TailObserver

        observers.append(TailObserver())
    if ev_is_true("ENABLE_WHISKER"):
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
        idle_timeout_secs=None,  # Disable idle timeout; Twilio's on_session_timeout handles timeouts
        observers=observers,
    )

    # Initialize flow manager with LLM
    flow_manager = FlowManager(
        task=task,
        llm=llm,
        context_aggregator=context_aggregator,
        global_functions=[
            caller_ended_conversation,
            end_conversation,
        ],
    )

    # Add flow manager state from Twilio's call_data
    flow_manager.state["call_id"] = call_data["call_id"]
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
        await task.stop_when_done()

    @task.event_handler("on_pipeline_finished")
    async def on_pipeline_finished(task, frame):
        log_flow_manager_state(flow_manager)
        await save_state_to_json(flow_manager.state)
        await save_intake_legalserver(flow_manager.state)

    @audiobuffer.event_handler("on_audio_data")
    async def on_audio_data(buffer, audio, sample_rate, num_channels):
        if ev_is_true("SAVE_AUDIO_RECORDINGS"):
            await save_audio(audio, sample_rate, num_channels)

    @transcript.event_handler("on_transcript_update")
    async def on_transcript_update(processor, frame):
        await transcript_handler.on_transcript_update(processor, frame)

    # We use `handle_sigint=False` because `uvicorn` (not sure if this
    # applies since we're using `granian` now) is controlling keyboard
    # interruptions. We use `force_gc=True` to force garbage collection
    # after the runner finishes running a task which could be useful for
    # long running applications with multiple clients connecting.

    if ev_is_true("ENABLE_TAIL_RUNNER"):
        from pipecat_tail.runner import TailRunner

        runner = TailRunner(handle_sigint=handle_sigint, force_gc=True)
        await runner.run(task)
    else:
        runner = PipelineRunner(handle_sigint=handle_sigint, force_gc=True)
        await runner.run(task)


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
