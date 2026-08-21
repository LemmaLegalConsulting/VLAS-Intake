from __future__ import annotations

import asyncio
import copy
import uuid
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Self

from pipecat.flows import NodeConfig
from pipecat.frames.frames import (
    EndFrame,
    FunctionCallFromLLM,
    LLMAssistantPushAggregationFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import LLMService
from pipecat.services.settings import LLMSettings
from pipecat.transcriptions.language import Language
from pipecat.workers.runner import WorkerRunner

from intake_bot.bot import StateContextFlowManager, build_context_aggregator
from intake_bot.nodes.nodes import (
    NodeDependencies,
    caller_ended_conversation,
    end_conversation,
    node_start,
)


@dataclass(frozen=True)
class ScriptedFunctionCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ScriptedLLMResponse:
    text: str | None = None
    function_call: ScriptedFunctionCall | None = None

    def __post_init__(self) -> None:
        if self.text and self.function_call:
            raise ValueError(
                "A scripted response cannot contain text and a function call"
            )
        if not self.text and not self.function_call:
            raise ValueError("A scripted response must contain text or a function call")


@dataclass(frozen=True)
class ScriptedLLMCall:
    messages: tuple[Any, ...]
    tool_names: tuple[str, ...]


class ScriptedLLMService(LLMService):
    """Deterministic LLM service that still exercises Pipecat tool dispatch."""

    def __init__(self, responses: Iterable[ScriptedLLMResponse]):
        super().__init__(
            settings=LLMSettings(
                model="scripted",
                system_instruction=None,
                temperature=None,
                max_tokens=None,
                top_p=None,
                top_k=None,
                frequency_penalty=None,
                presence_penalty=None,
                seed=None,
                filter_incomplete_user_turns=None,
                user_turn_completion_config=None,
            )
        )
        self._responses = deque(responses)
        self.calls: list[ScriptedLLMCall] = []
        self._tool_call_counter = 0

    async def run_inference(
        self,
        context: LLMContext,
        max_tokens: int | None = None,
        system_instruction: str | None = None,
    ) -> str | None:
        raise NotImplementedError("ScriptedLLMService only supports pipeline inference")

    async def run_function_calls(self, function_calls):
        """Wait for scripted tool handlers so text turns have deterministic completion."""
        await super().run_function_calls(function_calls)
        tasks = tuple(task for task in self._function_call_tasks if task is not None)
        if tasks:
            await asyncio.gather(*tasks)

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMContextFrame):
            tools = getattr(frame.context.tools, "standard_tools", ())
            self.calls.append(
                ScriptedLLMCall(
                    messages=tuple(copy.deepcopy(frame.context.get_messages())),
                    tool_names=tuple(tool.name for tool in tools),
                )
            )
            if not self._responses:
                raise AssertionError(
                    "ScriptedLLMService received an unexpected LLM request"
                )

            response = self._responses.popleft()
            await self.push_frame(LLMFullResponseStartFrame())
            if response.function_call is not None:
                self._tool_call_counter += 1
                await self.run_function_calls(
                    [
                        FunctionCallFromLLM(
                            function_name=response.function_call.name,
                            tool_call_id=f"scripted-call-{self._tool_call_counter}",
                            arguments=response.function_call.arguments,
                            context=frame.context,
                        )
                    ]
                )
            else:
                await self.push_frame(LLMTextFrame(response.text or ""))
            await self.push_frame(LLMFullResponseEndFrame())
            return

        await self.push_frame(frame, direction)


class TextOutputAdapter(FrameProcessor):
    """Replace audio TTS with text while preserving assistant context semantics."""

    def __init__(self):
        super().__init__()
        self.outputs: list[str] = []
        self._announced_text_counts: dict[str, int] = {}
        self.finished = False
        self._llm_response_text: list[str] | None = None

    def record_spoken_text(self, text: str) -> None:
        if not text:
            return
        self.outputs.append(text)
        self._announced_text_counts[text] = self._announced_text_counts.get(text, 0) + 1

    async def process_frame(self, frame, direction: FrameDirection):
        if isinstance(frame, EndFrame):
            self.finished = True
        await super().process_frame(frame, direction)

        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseStartFrame):
            self._llm_response_text = []
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMTextFrame):
            if self._llm_response_text is not None:
                self._llm_response_text.append(frame.text)
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            if self._llm_response_text:
                response = "".join(self._llm_response_text)
                if response.strip():
                    self.outputs.append(response)
            self._llm_response_text = None
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TTSSpeakFrame):
            announced_count = self._announced_text_counts.get(frame.text, 0)
            if announced_count:
                if announced_count == 1:
                    self._announced_text_counts.pop(frame.text)
                else:
                    self._announced_text_counts[frame.text] = announced_count - 1
            else:
                self.outputs.append(frame.text)
            context_id = str(uuid.uuid4())
            await self.push_frame(
                TTSStartedFrame(
                    context_id=context_id,
                    append_to_context=frame.append_to_context,
                ),
                direction,
            )
            tts_text = TTSTextFrame(frame.text, aggregated_by="sentence")
            tts_text.includes_inter_frame_spaces = True
            tts_text.append_to_context = frame.append_to_context
            tts_text.context_id = context_id
            await self.push_frame(tts_text, direction)
            await self.push_frame(TTSStoppedFrame(context_id=context_id), direction)
            if frame.append_to_context:
                await self.push_frame(LLMAssistantPushAggregationFrame(), direction)
            return

        await self.push_frame(frame, direction)


class _TextTranscriptHandler:
    def __init__(self, output: TextOutputAdapter):
        self._output = output

    async def save_assistant_tts(self, content: str) -> None:
        self._output.record_spoken_text(content)


@dataclass(frozen=True)
class TextTurnResult:
    user_text: str
    assistant_text: tuple[str, ...]
    state: dict[str, Any]
    current_node: str | None


class _TextFlowManager(StateContextFlowManager):
    def __init__(
        self,
        *,
        node_set_event: asyncio.Event,
        terminal_event: asyncio.Event,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._node_set_event = node_set_event
        self._terminal_event = terminal_event

    async def _set_node(self, node_id: str, node_config: NodeConfig) -> None:
        await super()._set_node(node_id, node_config)
        self._node_set_event.set()
        if any(
            action.get("type") == "end_conversation"
            for action in node_config.get("post_actions", [])
        ):
            self._terminal_event.set()


class TextSession:
    """Run the real intake flow without STT, TTS, transport, or network calls."""

    def __init__(
        self,
        *,
        llm: LLMService,
        node_dependencies: NodeDependencies | None = None,
        initial_node: NodeConfig | None = None,
        call_id: str = "text-session",
        caller_phone_number: str = "",
        flush_timeout_secs: float = 5.0,
    ):
        self.llm = llm
        self._node_dependencies = node_dependencies
        self._initial_node = initial_node
        self._call_id = call_id
        self._caller_phone_number = caller_phone_number
        self._flush_timeout_secs = flush_timeout_secs
        self._started = False
        self._finished = False
        self._closed = False
        self._runner_task: asyncio.Task | None = None
        self._assistant_response = asyncio.Event()
        self._function_call_started = asyncio.Event()
        self._node_set_event = asyncio.Event()
        self._terminal_event = asyncio.Event()
        self._pipeline_started = asyncio.Event()
        self.context_aggregator = build_context_aggregator(
            user_idle_timeout_secs=0,
            external_turn_stop_timeout_secs=0,
        )
        self.output = TextOutputAdapter()
        self.pipeline = Pipeline(
            [
                self.context_aggregator.user(),
                self.llm,
                self.output,
                self.context_aggregator.assistant(),
            ]
        )
        self.worker = PipelineWorker(
            self.pipeline,
            params=PipelineParams(enable_metrics=False, enable_usage_metrics=False),
            cancel_on_idle_timeout=False,
        )
        self.runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
        self.flow_manager = _TextFlowManager(
            worker=self.worker,
            llm=self.llm,
            context_aggregator=self.context_aggregator,
            global_functions=[caller_ended_conversation, end_conversation],
            node_set_event=self._node_set_event,
            terminal_event=self._terminal_event,
        )
        self.flow_manager._transcript_handler = _TextTranscriptHandler(self.output)
        self.flow_manager.state["call_id"] = call_id
        self.flow_manager.state["phone"] = caller_phone_number
        self.flow_manager._tts_services = {
            Language.EN: object(),
            Language.ES: object(),
        }
        if node_dependencies is not None:
            self.flow_manager._node_dependencies = node_dependencies

        @self.worker.event_handler("on_pipeline_started")
        async def _on_pipeline_started(worker, frame):
            self._pipeline_started.set()

        @self.worker.event_handler("on_pipeline_finished")
        async def _on_pipeline_finished(worker, frame):
            self._finished = True

        @self.context_aggregator.assistant().event_handler("on_assistant_turn_stopped")
        async def _on_assistant_turn_stopped(aggregator, message):
            self._assistant_response.set()

        @self.llm.event_handler("on_function_calls_started")
        async def _on_function_calls_started(service, function_calls):
            self._function_call_started.set()

    @property
    def finished(self) -> bool:
        return self._finished or self.output.finished or self._terminal_event.is_set()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.close()

    async def start(self) -> TextTurnResult:
        if self._closed:
            raise RuntimeError("TextSession is closed")
        if self._started:
            raise RuntimeError("TextSession is already started")

        await self.runner.add_workers(self.worker)
        self._runner_task = asyncio.create_task(self.runner.run(auto_end=False))
        try:
            await asyncio.wait_for(
                self._pipeline_started.wait(), timeout=self._flush_timeout_secs
            )
            await self.flow_manager.initialize(self._initial_node or node_start())
            self._node_set_event.clear()
        except BaseException:
            await self.close()
            raise

        self._started = True
        return TextTurnResult(
            user_text="",
            assistant_text=tuple(self.output.outputs),
            state=copy.deepcopy(self.flow_manager.state),
            current_node=self.flow_manager.current_node,
        )

    async def send_user_turn(self, text: str) -> TextTurnResult:
        if not self._started or self._closed:
            raise RuntimeError("TextSession must be started and open")

        output_start = len(self.output.outputs)
        self._assistant_response.clear()
        self._function_call_started.clear()
        self._node_set_event.clear()
        timestamp = datetime.now(UTC).isoformat(timespec="milliseconds")
        await self.worker.queue_frames(
            [
                UserStartedSpeakingFrame(),
                TranscriptionFrame(
                    text=text,
                    user_id="text-test-caller",
                    timestamp=timestamp,
                    finalized=True,
                ),
                UserStoppedSpeakingFrame(),
            ]
        )
        await asyncio.wait_for(
            self._assistant_response.wait(), timeout=self._flush_timeout_secs
        )
        if self._function_call_started.is_set():
            try:
                await asyncio.wait_for(
                    self._node_set_event.wait(), timeout=self._flush_timeout_secs
                )
            except TimeoutError:
                pass
        await self._flush()
        return TextTurnResult(
            user_text=text,
            assistant_text=tuple(self.output.outputs[output_start:]),
            state=copy.deepcopy(self.flow_manager.state),
            current_node=self.flow_manager.current_node,
        )

    async def _flush(self) -> None:
        previous_signature: tuple[str | None, int, bool, bool, bool, bool] | None = None
        stable_polls = 0
        max_polls = max(20, int(self._flush_timeout_secs / 0.01))
        for _ in range(max_polls):
            if not await self.worker.flush_pipeline(timeout=self._flush_timeout_secs):
                raise TimeoutError("TextSession pipeline did not drain")

            await asyncio.sleep(0)
            signature = (
                self.flow_manager.current_node,
                len(self.output.outputs),
                self.finished,
                self.context_aggregator.assistant().has_function_calls_in_progress,
                bool(getattr(self.llm, "_function_call_tasks", {})),
                bool(getattr(self.flow_manager, "_pending_transition", None)),
            )
            if signature == previous_signature:
                stable_polls += 1
                if stable_polls >= 2:
                    return
            else:
                previous_signature = signature
                stable_polls = 0
            await asyncio.sleep(0.01)

        raise TimeoutError("TextSession pipeline did not reach a stable state")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._runner_task is not None:
            await self.runner.cancel(reason="text session closed")
            try:
                await asyncio.wait_for(
                    self._runner_task, timeout=self._flush_timeout_secs
                )
            except (asyncio.CancelledError, TimeoutError):
                if not self._runner_task.done():
                    self._runner_task.cancel()
                await asyncio.gather(self._runner_task, return_exceptions=True)
