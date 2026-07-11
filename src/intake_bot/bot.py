import json
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from datetime import datetime, timezone

import aiofiles
from loguru import logger
from pipecat.frames.frames import (
    EndFrame,
    TTSSpeakFrame,
    UserIdleTimeoutUpdateFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.workers.runner import WorkerRunner
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    AssistantTurnStoppedMessage,
    LLMAssistantAggregatorParams,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
    UserTurnStoppedMessage,
)
from pipecat.runner.types import DailyDialinRequest, RunnerArguments
from pipecat.services.azure.llm import AzureLLMService
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.daily.transport import (
    DailyDialinSettings,
    DailyParams,
    DailyTransport,
)
from pipecat.turns.user_mute import (
    AlwaysUserMuteStrategy,
    FunctionCallUserMuteStrategy,
)
from pipecat.turns.user_start.external_user_turn_start_strategy import (
    ExternalUserTurnStartStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.flows import ContextStrategy, FlowManager
from pydantic import ValidationError

from openai import (
    APIError as OpenAIAPIError,
    RateLimitError as OpenAIRateLimitError,
    APIConnectionError,
    APITimeoutError,
)

from intake_bot.nodes.nodes import (
    caller_ended_conversation,
    end_conversation,
    node_start,
)
from intake_bot.nodes.utils import log_flow_manager_state, save_state_to_json
from intake_bot.services.legalserver import save_intake_legalserver
from intake_bot.turn_strategies import DeduplicatingExternalUserTurnStopStrategy
from intake_bot.utils.call_logging import call_logging_context, transcript_log_path
from intake_bot.utils.daily_dialin import (
    looks_like_daily_dialin_body,
    normalize_daily_dialin_body,
)
from intake_bot.utils.ev import ev_is_true, get_deepgram_tts_voices, get_ev, require_ev
from intake_bot.utils.node_prompts import NodePrompts

TransportSetup = Callable[
    [BaseTransport, PipelineWorker, FlowManager, str], Awaitable[None]
]


class StateContextFlowManager(FlowManager):
    _STATE_CONTEXT_EXCLUDED_TOP_LEVEL_KEYS = {
        "_transcript_handler",
        "_adverse_parties_follow_up_requested",
        "call_id",
        "sms_messages",
        "status",
        "error",
        "tts_voice",
    }

    def _trim_state_context_value(self, value):
        if value is None:
            return None

        if isinstance(value, dict):
            trimmed = {}
            for key, item in value.items():
                if isinstance(key, str) and key.startswith("_"):
                    continue
                trimmed_item = self._trim_state_context_value(item)
                if trimmed_item is None:
                    continue
                if trimmed_item == "":
                    continue
                if trimmed_item == []:
                    continue
                if trimmed_item == {}:
                    continue
                trimmed[key] = trimmed_item
            return trimmed or None

        if isinstance(value, list):
            trimmed = [
                item
                for item in (self._trim_state_context_value(item) for item in value)
                if item not in (None, "", [], {})
            ]
            return trimmed or None

        return value

    def _build_state_context_message(self) -> dict | None:
        trimmed_state = {}
        for key, value in self.state.items():
            if key in self._STATE_CONTEXT_EXCLUDED_TOP_LEVEL_KEYS:
                continue
            if isinstance(key, str) and key.startswith("_"):
                continue

            trimmed_value = self._trim_state_context_value(value)
            if trimmed_value in (None, "", [], {}):
                continue
            trimmed_state[key] = trimmed_value

        if not trimmed_state:
            return None

        return {
            "role": "developer",
            "content": (
                "Caller data collected so far. Use this structured state for continuity and relevance. "
                "Treat it as the current known intake state, not as wording to repeat verbatim.\n"
                f"{json.dumps(trimmed_state, ensure_ascii=True, separators=(',', ':'))}"
            ),
        }

    async def _update_llm_context(
        self,
        role_message,
        role_messages,
        task_messages,
        functions,
        strategy=None,
    ):
        update_config = strategy or self._context_strategy
        effective_task_messages = list(task_messages)

        if (
            self._current_node is not None
            and update_config.strategy == ContextStrategy.RESET
        ):
            state_context_message = self._build_state_context_message()
            if state_context_message is not None:
                effective_task_messages.insert(0, state_context_message)

        await super()._update_llm_context(
            role_message,
            role_messages,
            effective_task_messages,
            functions,
            strategy,
        )


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

    async def save_assistant_tts(self, content: str) -> None:
        if not content or not content.strip():
            return

        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        await self.save_transcript_message("assistant", content, timestamp)

    async def on_user_transcript(
        self, aggregator, strategy, message: UserTurnStoppedMessage
    ):
        """Handle new user transcript message."""
        await self.save_transcript_message("user", message.content, message.timestamp)

    async def on_assistant_transcript(
        self, aggregator, message: AssistantTurnStoppedMessage
    ):
        """Handle new assistant transcript message."""
        if not message.content or not message.content.strip():
            return
        await self.save_transcript_message(
            "assistant", message.content, message.timestamp
        )


class IdleRetryHandler:
    """Tracks idle reminders and returns the next frames to queue."""

    def __init__(
        self, prompts: NodePrompts | None = None, prompt_prefix: str = "idle_retry"
    ):
        self._retry_count = 0
        self._prompts = prompts or NodePrompts()
        self._prompt_prefix = prompt_prefix

    def reset(self) -> None:
        self._retry_count = 0

    def next_frames(self, language: str) -> list[TTSSpeakFrame | EndFrame]:
        self._retry_count += 1

        if self._retry_count == 1:
            msg = self._prompts.get_spoken_prompt(
                f"{self._prompt_prefix}_first", language
            )
            return [TTSSpeakFrame(msg, append_to_context=False)]

        if self._retry_count == 2:
            msg = self._prompts.get_spoken_prompt(
                f"{self._prompt_prefix}_second", language
            )
            return [TTSSpeakFrame(msg, append_to_context=False)]

        goodbye = self._prompts.get_spoken_prompt(
            f"{self._prompt_prefix}_goodbye", language
        )

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
        async def configure_daily_transport(transport, worker, flow_manager, call_id):
            flow_initialized = False

            @transport.event_handler("on_first_participant_joined")
            async def on_first_participant_joined(transport, participant):
                nonlocal flow_initialized
                if flow_initialized:
                    return

                flow_initialized = True
                logger.info(log_message.format(call_id=call_id))
                await flow_manager.initialize(node_start())

        return configure_daily_transport

    if not looks_like_daily_dialin_body(body):
        logger.info(
            "No Daily dial-in metadata detected; starting standard Pipecat Cloud WebRTC session."
        )
        transport = DailyTransport(
            runner_args.room_url,
            runner_args.token,
            "VLAS Intake Bot",
            params=DailyParams(
                audio_in_enabled=True,
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

    transport = DailyTransport(
        runner_args.room_url,
        runner_args.token,
        "VLAS Intake Bot",
        params=DailyParams(
            api_key=request.daily_api_key,
            api_url=request.daily_api_url,
            dialin_settings=daily_dialin_settings,
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    )

    configure_daily_transport = build_daily_participant_initializer(
        "First PSTN participant joined call {call_id}"
    )

    async def configure_daily_transport_with_dialin_error(
        transport, worker, flow_manager, call_id
    ):
        await configure_daily_transport(transport, worker, flow_manager, call_id)

        @transport.event_handler("on_dialin_error")
        async def on_dialin_error(transport, data):
            logger.error(f"""Dial-in error: {data}""")
            await worker.cancel()

    await run_bot(
        transport,
        call_id,
        caller_phone_number,
        runner_args.handle_sigint,
        configure_transport=configure_daily_transport_with_dialin_error,
    )


def _get_flux_settings(call_id: str) -> dict:
    default_eager_eot = "0.3" if call_id.startswith("ws-test") else "0.6"
    default_eot = "0.3" if call_id.startswith("ws-test") else "0.6"
    default_eot_timeout = "1500" if call_id.startswith("ws-test") else "800"
    default_min_conf = "0.1" if call_id.startswith("ws-test") else "0.5"
    return {
        "eager_eot_threshold": float(
            get_ev("DEEPGRAM_FLUX_EAGER_EOT_THRESHOLD", default_eager_eot)
        ),
        "eot_threshold": float(get_ev("DEEPGRAM_FLUX_EOT_THRESHOLD", default_eot)),
        "eot_timeout_ms": int(
            get_ev("DEEPGRAM_FLUX_EOT_TIMEOUT_MS", default_eot_timeout)
        ),
        "min_confidence": float(
            get_ev("DEEPGRAM_FLUX_MIN_CONFIDENCE", default_min_conf)
        ),
    }


async def run_bot(
    transport: BaseTransport,
    call_id: str,
    caller_phone_number: str,
    handle_sigint: bool,
    configure_transport: TransportSetup | None = None,
    user_idle_timeout_secs: float | None = None,
    strict_user_muting: bool = False,
):
    """
    Main function to set up and run the VLAS intake bot.
    """
    with ExitStack() as exit_stack:
        exit_stack.enter_context(call_logging_context(call_id))

        if caller_phone_number:
            logger.info(f"""Handling incoming call from: {caller_phone_number}""")

        flux_settings = _get_flux_settings(call_id)
        flux_eager_eot_threshold = flux_settings["eager_eot_threshold"]
        flux_eot_threshold = flux_settings["eot_threshold"]
        flux_eot_timeout_ms = flux_settings["eot_timeout_ms"]
        flux_min_confidence = flux_settings["min_confidence"]

        stt = DeepgramFluxSTTService(
            api_key=require_ev("DEEPGRAM_API_KEY"),
            ttfs_p99_latency=float(get_ev("DEEPGRAM_STT_TTFS_P99_LATENCY", "0.35")),
            settings=DeepgramFluxSTTService.Settings(
                model=get_ev("DEEPGRAM_STT_MODEL", "flux-general-multi"),
                language_hints=[Language.EN, Language.ES],
                eager_eot_threshold=flux_eager_eot_threshold,
                eot_threshold=flux_eot_threshold,
                eot_timeout_ms=flux_eot_timeout_ms,
                min_confidence=flux_min_confidence,
            ),
        )

        tts_voice = get_deepgram_tts_voices(Language.EN)

        llm = AzureLLMService(
            api_key=require_ev("AZURE_API_KEY"),
            endpoint=require_ev("AZURE_LLM_ENDPOINT"),
            settings=AzureLLMService.Settings(
                model=require_ev("AZURE_LLM_MODEL"),
            ),
        )

        tts = DeepgramTTSService(
            api_key=require_ev("DEEPGRAM_API_KEY"),
            settings=DeepgramTTSService.Settings(
                voice=tts_voice,
            ),
        )

        resolved_user_idle_timeout_secs = user_idle_timeout_secs
        if resolved_user_idle_timeout_secs is None:
            resolved_user_idle_timeout_secs = float(
                get_ev("USER_IDLE_TIMEOUT_SECS", "15.0")
            )

        context = LLMContext()
        external_turn_stop_timeout_secs = float(
            get_ev("EXTERNAL_TURN_STOP_TIMEOUT_SECS", "0.2")
        )
        user_mute_strategies = [FunctionCallUserMuteStrategy()]
        if strict_user_muting:
            user_mute_strategies.append(AlwaysUserMuteStrategy())

        context_aggregator = LLMContextAggregatorPair(
            context,
            assistant_params=LLMAssistantAggregatorParams(),
            user_params=LLMUserAggregatorParams(
                filter_incomplete_user_turns=False,
                user_mute_strategies=user_mute_strategies,
                user_idle_timeout=resolved_user_idle_timeout_secs,
                user_turn_strategies=UserTurnStrategies(
                    start=[ExternalUserTurnStartStrategy()],
                    stop=[
                        DeduplicatingExternalUserTurnStopStrategy(
                            timeout=external_turn_stop_timeout_secs
                        )
                    ],
                ),
            ),
        )

        logger.info(
            f"""Using user idle timeout of {resolved_user_idle_timeout_secs:.1f}s and external turn stop timeout of {external_turn_stop_timeout_secs:.2f}s for call {call_id}"""
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

        transcript_file = None
        if ev_is_true("LOG_TO_FILE"):
            transcript_file = transcript_log_path(call_id)
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

        worker = PipelineWorker(
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

        flow_manager = StateContextFlowManager(
            worker=worker,
            llm=llm,
            context_aggregator=context_aggregator,
            global_functions=[
                caller_ended_conversation,
                end_conversation,
            ],
        )

        flow_manager.state["call_id"] = call_id
        flow_manager.state["phone"] = caller_phone_number
        flow_manager._transcript_handler = transcript_handler

        idle_retry_handler = IdleRetryHandler()
        empty_turn_retry_handler = IdleRetryHandler(prompt_prefix="empty_turn_retry")

        async def queue_idle_frames_with_transcript(
            frames: list[TTSSpeakFrame | EndFrame],
        ) -> None:
            for frame in frames:
                if isinstance(frame, TTSSpeakFrame):
                    await transcript_handler.save_assistant_tts(frame.text)
            await worker.queue_frames(frames)

        @context_aggregator.user().event_handler("on_user_turn_started")
        async def on_user_turn_started(aggregator, strategy):
            idle_retry_handler.reset()
            empty_turn_retry_handler.reset()

        @context_aggregator.user().event_handler("on_user_turn_stopped")
        async def on_empty_user_turn_recovery(
            aggregator, strategy, message: UserTurnStoppedMessage
        ):
            if not message.content or not message.content.strip():
                logger.warning(
                    f"""Empty user turn detected for call {call_id}; triggering empty-turn recovery"""
                )
                language = flow_manager.state.get("language", {}).get(
                    "language", "English"
                )
                await queue_idle_frames_with_transcript(
                    empty_turn_retry_handler.next_frames(language)
                )

        @context_aggregator.user().event_handler("on_user_turn_idle")
        async def on_user_turn_idle(aggregator):
            language = flow_manager.state.get("language", {}).get("language", "English")
            await queue_idle_frames_with_transcript(
                idle_retry_handler.next_frames(language)
            )

        @context_aggregator.assistant().event_handler("on_assistant_turn_stopped")
        async def on_assistant_turn_stopped(
            aggregator, message: AssistantTurnStoppedMessage
        ):
            timeout_secs = adaptive_idle_timeout.timeout_for_content(message.content)
            if timeout_secs > resolved_user_idle_timeout_secs:
                logger.debug(
                    f"""Extending user idle timeout to {timeout_secs:.1f}s after assistant turn with {len(message.content.split())} words"""
                )
            await worker.queue_frame(UserIdleTimeoutUpdateFrame(timeout=timeout_secs))

        if configure_transport is not None:
            await configure_transport(transport, worker, flow_manager, call_id)

        @transport.event_handler("on_session_timeout")
        async def handle_timeout(transport, participant):
            logger.info("Call timed out; ending.")
            language = flow_manager.state.get("language", {}).get("language", "English")
            if language == "Spanish":
                timeout_msg = "Gracias por llamar al servicio de ayuda legal Law-Line de Virginia. Parece que se ha desconectado. No dude en volver a llamarnos. ¡Adiós!"
            else:
                timeout_msg = "Thank you for calling Virginia's Law-Line Legal Help Service. It seems that you have disconnected. Please feel free to call us back. Goodbye!"
            await worker.queue_frames(
                [
                    TTSSpeakFrame(timeout_msg),
                    EndFrame(),
                ]
            )

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info(f"""Client disconnected for call {call_id}""")
            await worker.stop_when_done()

        @worker.event_handler("on_pipeline_finished")
        async def on_pipeline_finished(worker, frame):
            log_flow_manager_state(flow_manager)
            await save_state_to_json(flow_manager.state)
            await save_intake_legalserver(flow_manager.state)

        _llm_error_count = 0
        _last_llm_error_time = 0.0

        @worker.event_handler("on_pipeline_error")
        async def on_pipeline_error(worker, error):
            nonlocal _llm_error_count, _last_llm_error_time
            error_name = type(error).__name__
            error_msg = str(error)
            logger.warning(
                f"Pipeline error in call {call_id}: {error_name}: {error_msg}"
            )
            error_name_lower = error_name.lower()
            error_msg_lower = error_msg.lower()
            if isinstance(
                error,
                (
                    OpenAIAPIError,
                    OpenAIRateLimitError,
                    APIConnectionError,
                    APITimeoutError,
                ),
            ) or (
                "completion" in error_msg_lower
                or "llm" in error_name_lower
                or "openai" in error_name_lower
            ):
                now = datetime.now(timezone.utc).timestamp()
                if now - _last_llm_error_time < 30.0:
                    _llm_error_count += 1
                else:
                    _llm_error_count = 1
                _last_llm_error_time = now
                if _llm_error_count >= 3:
                    logger.error(
                        f"Too many LLM completion errors ({_llm_error_count}) in call {call_id}; ending call"
                    )
                    language = flow_manager.state.get("language", {}).get(
                        "language", "English"
                    )
                    if language == "Spanish":
                        msg = "Lo siento, tenemos problemas técnicos. Por favor, intente llamar de nuevo más tarde. ¡Gracias y adiós!"
                    else:
                        msg = "I'm sorry, we are experiencing technical difficulties. Please try calling again later. Thank you and goodbye!"
                    await worker.queue_frames(
                        [
                            TTSSpeakFrame(msg),
                            EndFrame(),
                        ]
                    )

        if ev_is_true("ENABLE_TAIL_RUNNER"):
            from pipecat_tail.runner import TailRunner

            runner = TailRunner(handle_sigint=handle_sigint, force_gc=True)
            await runner.add_workers(worker)
            await runner.run()
        else:
            runner = WorkerRunner(handle_sigint=handle_sigint, force_gc=True)
            await runner.add_workers(worker)
            await runner.run()


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
