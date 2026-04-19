import os
from collections.abc import Awaitable, Callable

import aiofiles
from loguru import logger
from pipecat.audio.filters.rnnoise_filter import RNNoiseFilter

try:
    from pipecat.audio.filters.krisp_viva_filter import KrispVivaFilter
except Exception:
    KrispVivaFilter = None

from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    EndFrame,
    TTSSpeakFrame,
    UserIdleTimeoutUpdateFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    AssistantTurnStoppedMessage,
    LLMAssistantAggregatorParams,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
    UserTurnStoppedMessage,
)
from pipecat.utils.context.llm_context_summarization import (
    LLMAutoContextSummarizationConfig,
    LLMContextSummaryConfig,
)
from pipecat.runner.types import DailyDialinRequest, RunnerArguments
from pipecat.services.azure.llm import AzureLLMService
from pipecat.services.azure.stt import AzureSTTService
from pipecat.services.azure.tts import AzureTTSService
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.daily.transport import (
    DailyDialinSettings,
    DailyParams,
    DailyTransport,
)
from pipecat.turns.user_mute import (
    FunctionCallUserMuteStrategy,
)
from pipecat.turns.user_start import MinWordsUserTurnStartStrategy
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat_flows import FlowManager
from pydantic import ValidationError

from intake_bot.nodes.nodes import (
    caller_ended_conversation,
    end_conversation,
    node_initial,
)
from intake_bot.nodes.utils import log_flow_manager_state, save_state_to_json
from intake_bot.services.legalserver import save_intake_legalserver
from intake_bot.utils.daily_dialin import (
    looks_like_daily_dialin_body,
    normalize_daily_dialin_body,
)
from intake_bot.utils.ev import ev_is_true, get_ev, require_ev
from intake_bot.utils.node_prompts import NodePrompts

TransportSetup = Callable[
    [BaseTransport, PipelineTask, FlowManager, str], Awaitable[None]
]


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
        self.output_file: str | None = output_file
        logger.debug(
            f"""TranscriptHandler initialized {"with output_file=" + output_file if output_file else "with log output only"}"""
        )

    async def save_transcript_message(
        self, role: str, content: str, timestamp: str = ""
    ):
        """Save a single transcript message.

        Outputs the message to the log and optionally to a file.
        """
        timestamp_str = f"""[{timestamp}] """ if timestamp else ""
        line = f"""{timestamp_str}{role}: {content}"""

        # Always log the message
        logger.debug(f"""Transcript: {line}""")

        # Optionally write to file
        if self.output_file:
            try:
                async with aiofiles.open(self.output_file, "a", encoding="utf-8") as f:
                    await f.write(line + "\n")
            except Exception as e:
                logger.error(f"""Error saving transcript message to file: {e}""")

    async def on_user_transcript(
        self, aggregator, strategy, message: UserTurnStoppedMessage
    ):
        """Handle new user transcript message."""
        await self.save_transcript_message("user", message.content, message.timestamp)

    async def on_assistant_transcript(
        self, aggregator, message: AssistantTurnStoppedMessage
    ):
        """Handle new assistant transcript message."""
        await self.save_transcript_message(
            "assistant", message.content, message.timestamp
        )


class IdleRetryHandler:
    """Tracks idle reminders and returns the next frames to queue."""

    def __init__(self):
        self._retry_count = 0

    def reset(self) -> None:
        self._retry_count = 0

    def next_frames(self, language: str) -> list[TTSSpeakFrame | EndFrame]:
        self._retry_count += 1

        if self._retry_count == 1:
            if language == "Spanish":
                msg = "¿Sigue ahí?"
            else:
                msg = "Are you still there?"
            return [TTSSpeakFrame(msg, append_to_context=False)]

        if self._retry_count == 2:
            if language == "Spanish":
                msg = "¿Le gustaría continuar con la entrevista?"
            else:
                msg = "Would you like to continue with the interview?"
            return [TTSSpeakFrame(msg, append_to_context=False)]

        if language == "Spanish":
            goodbye = "Parece que está ocupado en este momento. No dude en volver a llamar. ¡Que tenga un buen día!"
        else:
            goodbye = "It seems like you're busy right now. Feel free to call back. Have a nice day!"

        return [TTSSpeakFrame(goodbye, append_to_context=False), EndFrame()]


class AdaptiveIdleTimeout:
    """Extends the user idle timeout after longer assistant turns."""

    def __init__(
        self,
        base_timeout_secs: float,
        max_timeout_secs: float,
        words_per_extra_second: float,
    ):
        self.base_timeout_secs = base_timeout_secs
        self.max_timeout_secs = max(base_timeout_secs, max_timeout_secs)
        self.words_per_extra_second = max(words_per_extra_second, 1.0)

    def timeout_for_content(self, content: str) -> float:
        word_count = len(content.split())
        extra_timeout_secs = min(
            self.max_timeout_secs - self.base_timeout_secs,
            float(int(word_count / self.words_per_extra_second)),
        )
        return self.base_timeout_secs + extra_timeout_secs


async def bot(runner_args: RunnerArguments):
    """Main bot entry point for Daily local and Pipecat Cloud runtimes."""
    body = runner_args.body if isinstance(runner_args.body, dict) else {}
    logger.info(
        f"""Inbound bot invoked. body_type={type(runner_args.body).__name__}, body_keys={sorted(body.keys())}, room_url_present={bool(runner_args.room_url)}"""
    )

    def build_daily_participant_initializer(log_message: str):
        async def configure_daily_transport(transport, task, flow_manager, call_id):
            flow_initialized = False

            @transport.event_handler("on_first_participant_joined")
            async def on_first_participant_joined(transport, participant):
                nonlocal flow_initialized
                if flow_initialized:
                    return

                flow_initialized = True
                logger.info(log_message.format(call_id=call_id))
                await flow_manager.initialize(node_initial())

        return configure_daily_transport

    if not looks_like_daily_dialin_body(body):
        logger.info(
            "No Daily dial-in metadata detected; starting standard Pipecat Cloud WebRTC session."
        )
        audio_filter = KrispVivaFilter() if KrispVivaFilter else RNNoiseFilter()
        logger.info(f"""Audio input filter: {type(audio_filter).__name__}""")
        transport = DailyTransport(
            runner_args.room_url,
            runner_args.token,
            "VLAS Intake Bot",
            params=DailyParams(
                audio_in_enabled=True,
                audio_in_filter=audio_filter,
                audio_out_enabled=True,
            ),
        )
        await run_bot(
            transport,
            call_id="sandbox-session",
            caller_phone_number="",
            handle_sigint=runner_args.handle_sigint,
            configure_transport=build_daily_participant_initializer(
                "First Daily WebRTC participant joined call {call_id}"
            ),
        )
        return

    try:
        request = DailyDialinRequest.model_validate(normalize_daily_dialin_body(body))
    except (ValidationError, ValueError) as e:
        logger.error(
            f"""Invalid Daily dial-in request: {e}. Received body keys: {sorted(body.keys())}. If you are using Pipecat Cloud automatic telephony, point the Daily number at the Pipecat Cloud /dialin webhook. If you are using a custom webhook server, forward dialin_settings plus Daily API credentials."""
        )
        return

    daily_dialin_settings = DailyDialinSettings(
        call_id=request.dialin_settings.call_id,
        call_domain=request.dialin_settings.call_domain,
    )

    caller_phone_number = request.dialin_settings.From or ""
    call_id = request.dialin_settings.call_id
    if caller_phone_number:
        logger.info(f"""Handling Daily PSTN call from: {caller_phone_number}""")

    audio_filter = KrispVivaFilter() if KrispVivaFilter else RNNoiseFilter()
    logger.info(f"""Audio input filter: {type(audio_filter).__name__}""")
    transport = DailyTransport(
        runner_args.room_url,
        runner_args.token,
        "VLAS Intake Bot",
        params=DailyParams(
            api_key=request.daily_api_key,
            api_url=request.daily_api_url,
            dialin_settings=daily_dialin_settings,
            audio_in_enabled=True,
            audio_in_filter=audio_filter,
            audio_out_enabled=True,
        ),
    )

    configure_daily_transport = build_daily_participant_initializer(
        "First PSTN participant joined call {call_id}"
    )

    async def configure_daily_transport_with_dialin_error(
        transport, task, flow_manager, call_id
    ):
        await configure_daily_transport(transport, task, flow_manager, call_id)

        @transport.event_handler("on_dialin_error")
        async def on_dialin_error(transport, data):
            logger.error(f"""Dial-in error: {data}""")
            await task.cancel()

    await run_bot(
        transport,
        call_id,
        caller_phone_number,
        runner_args.handle_sigint,
        configure_transport=configure_daily_transport_with_dialin_error,
    )


async def run_bot(
    transport: BaseTransport,
    call_id: str,
    caller_phone_number: str,
    handle_sigint: bool,
    configure_transport: TransportSetup | None = None,
    user_idle_timeout_secs: float | None = None,
):
    """
    Main function to set up and run the VLAS intake bot.
    """
    stt = AzureSTTService(
        api_key=require_ev("AZURE_API_KEY"),
        region=require_ev("AZURE_SPEECH_REGION"),
        ttfs_p99_latency=float(get_ev("AZURE_STT_TTFS_P99_LATENCY", "1.5")),
    )

    llm = AzureLLMService(
        api_key=require_ev("AZURE_API_KEY"),
        endpoint=require_ev("AZURE_LLM_ENDPOINT"),
        settings=AzureLLMService.Settings(
            model=require_ev("AZURE_LLM_MODEL"),
        ),
    )

    tts = AzureTTSService(
        api_key=require_ev("AZURE_API_KEY"),
        region=require_ev("AZURE_SPEECH_REGION"),
        settings=AzureTTSService.Settings(
            voice=require_ev("AZURE_SPEECH_VOICE"),
        ),
    )

    resolved_user_idle_timeout_secs = user_idle_timeout_secs
    if resolved_user_idle_timeout_secs is None:
        resolved_user_idle_timeout_secs = float(
            get_ev("USER_IDLE_TIMEOUT_SECS", "15.0")
        )

    summary_llm_model = get_ev(
        "AZURE_LLM_SUMMARY_MODEL", default=require_ev("AZURE_LLM_MODEL")
    )
    summary_llm = AzureLLMService(
        api_key=require_ev("AZURE_API_KEY"),
        endpoint=require_ev("AZURE_LLM_ENDPOINT"),
        settings=AzureLLMService.Settings(model=summary_llm_model),
    )
    prompts = NodePrompts()
    summarization_prompt = prompts.get("reset_with_summary")

    context = LLMContext()
    context_aggregator = LLMContextAggregatorPair(
        context,
        assistant_params=LLMAssistantAggregatorParams(
            enable_auto_context_summarization=True,
            auto_context_summarization_config=LLMAutoContextSummarizationConfig(
                max_context_tokens=6000,
                max_unsummarized_messages=30,
                summary_config=LLMContextSummaryConfig(
                    target_context_tokens=4000,
                    min_messages_after_summary=6,
                    summarization_prompt=summarization_prompt,
                    llm=summary_llm,
                ),
            ),
        ),
        user_params=LLMUserAggregatorParams(
            user_mute_strategies=[FunctionCallUserMuteStrategy()],
            user_idle_timeout=resolved_user_idle_timeout_secs,
            user_turn_strategies=UserTurnStrategies(
                start=[
                    MinWordsUserTurnStartStrategy(min_words=2),
                ],
                stop=[
                    TurnAnalyzerUserTurnStopStrategy(
                        turn_analyzer=LocalSmartTurnAnalyzerV3(
                            params=SmartTurnParams(
                                stop_secs=float(get_ev("SMART_TURN_STOP_SECS", "4.5")),
                                pre_speech_ms=float(
                                    get_ev("SMART_TURN_PRE_SPEECH_MS", "700")
                                ),
                            )
                        )
                    )
                ],
            ),
            user_turn_stop_timeout=float(get_ev("USER_TURN_STOP_TIMEOUT_SECS", "7.0")),
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(
                    confidence=float(get_ev("VAD_CONFIDENCE", "0.65")),
                    start_secs=float(get_ev("VAD_START_SECS", "0.4")),
                    stop_secs=float(get_ev("VAD_STOP_SECS", "0.2")),
                    min_volume=float(get_ev("VAD_MIN_VOLUME", "0.55")),
                )
            ),
        ),
    )

    logger.info(
        f"""Using user idle timeout of {resolved_user_idle_timeout_secs:.1f}s for call {call_id}"""
    )

    adaptive_idle_timeout = AdaptiveIdleTimeout(
        base_timeout_secs=resolved_user_idle_timeout_secs,
        max_timeout_secs=float(
            get_ev(
                "USER_IDLE_TIMEOUT_MAX_SECS",
                str(max(resolved_user_idle_timeout_secs, 25.0)),
            )
        ),
        words_per_extra_second=float(
            get_ev("USER_IDLE_TIMEOUT_WORDS_PER_EXTRA_SECOND", "12.0")
        ),
    )

    # Create transcript handler
    transcript_file = None
    if ev_is_true("LOG_TO_FILE"):
        os.makedirs("logs", exist_ok=True)
        transcript_file = f"""logs/transcript_{call_id}.txt"""
        logger.info(f"""Logging transcript to file: {transcript_file}""")
    transcript_handler = TranscriptHandler(output_file=transcript_file)

    context_aggregator.user().event_handler("on_user_turn_stopped")(
        transcript_handler.on_user_transcript
    )

    context_aggregator.assistant().event_handler("on_assistant_turn_stopped")(
        transcript_handler.on_assistant_transcript
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,  # Speech-To-Text
            context_aggregator.user(),
            llm,  # LLM
            tts,  # Text-To-Speech
            transport.output(),
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
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        idle_timeout_secs=None,
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

    flow_manager.state["call_id"] = call_id
    flow_manager.state["phone"] = caller_phone_number

    idle_retry_handler = IdleRetryHandler()

    @context_aggregator.user().event_handler("on_user_turn_started")
    async def on_user_turn_started(aggregator, strategy):
        idle_retry_handler.reset()

    @context_aggregator.user().event_handler("on_user_turn_stopped")
    async def on_empty_user_turn_recovery(
        aggregator, strategy, message: UserTurnStoppedMessage
    ):
        if not message.content or not message.content.strip():
            logger.warning(
                f"""Empty user turn detected for call {call_id}; triggering idle recovery"""
            )
            language = flow_manager.state.get("language", {}).get("language", "English")
            await task.queue_frames(idle_retry_handler.next_frames(language))

    @context_aggregator.user().event_handler("on_user_turn_idle")
    async def on_user_turn_idle(aggregator):
        language = flow_manager.state.get("language", {}).get("language", "English")
        await task.queue_frames(idle_retry_handler.next_frames(language))

    @context_aggregator.assistant().event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(
        aggregator, message: AssistantTurnStoppedMessage
    ):
        timeout_secs = adaptive_idle_timeout.timeout_for_content(message.content)
        if timeout_secs > resolved_user_idle_timeout_secs:
            logger.debug(
                f"""Extending user idle timeout to {timeout_secs:.1f}s after assistant turn with {len(message.content.split())} words"""
            )
        await task.queue_frame(UserIdleTimeoutUpdateFrame(timeout=timeout_secs))

    if configure_transport is not None:
        await configure_transport(transport, task, flow_manager, call_id)

    @transport.event_handler("on_session_timeout")
    async def handle_timeout(transport, participant):
        # Play timeout message before ending call
        logger.info("Call timed out; ending.")
        language = flow_manager.state.get("language", {}).get("language", "English")
        if language == "Spanish":
            timeout_msg = "Gracias por llamar al servicio de ayuda legal Law-Line de Virginia. Parece que se ha desconectado. No dude en volver a llamarnos. ¡Adiós!"
        else:
            timeout_msg = "Thank you for calling Virginia's Law-Line Legal Help Service. It seems that you have disconnected. Please feel free to call us back. Goodbye!"
        await task.queue_frames(
            [
                TTSSpeakFrame(timeout_msg),
                EndFrame(),
            ]
        )

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(f"""Client disconnected for call {call_id}""")
        await task.stop_when_done()

    @task.event_handler("on_pipeline_finished")
    async def on_pipeline_finished(task, frame):
        log_flow_manager_state(flow_manager)
        await save_state_to_json(flow_manager.state)
        await save_intake_legalserver(flow_manager.state)

    # We use `handle_sigint=False` because `uvicorn` is controlling keyboard
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


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
