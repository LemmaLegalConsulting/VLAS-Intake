import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from intake_bot.nodes.nodes import (
    NodeDependencies,
    node_record_language,
    node_record_service_area,
)
from intake_bot.testing.text_pipeline import (
    ScriptedFunctionCall,
    ScriptedLLMResponse,
    ScriptedLLMService,
    TextSession,
)
from tests.fixtures.text_scenarios import (
    SERVICE_AREA_EXPECTED_STATE,
    SERVICE_AREA_TURN,
)


class FakeValidator:
    async def check_service_area(self, *, location: str) -> dict[str, Any]:
        assert location == SERVICE_AREA_TURN
        return {
            "outcome": "exact_match",
            "location": "Amelia County",
            "is_eligible": True,
            "fips_code": 51007,
        }


class DelayedValidator(FakeValidator):
    async def check_service_area(self, *, location: str) -> dict[str, Any]:
        await asyncio.sleep(0.02)
        return await super().check_service_area(location=location)


class NonWaitingScriptedLLMService(ScriptedLLMService):
    async def run_function_calls(self, function_calls):
        from pipecat.services.llm_service import LLMService

        await LLMService.run_function_calls(self, function_calls)


@dataclass
class FakeSMS:
    is_configured: bool = False

    async def send(self, phone_number: str, message: str) -> dict[str, Any]:
        raise AssertionError("SMS should not be used in this test")


@pytest.mark.asyncio
async def test_text_session_runs_flow_function_and_returns_next_prompt():
    llm = ScriptedLLMService(
        [
            ScriptedLLMResponse(
                function_call=ScriptedFunctionCall(
                    name="record_service_area",
                    arguments={"location": SERVICE_AREA_TURN},
                )
            )
        ]
    )
    dependencies = NodeDependencies(
        validator=FakeValidator(),
        sms_service=FakeSMS(),
    )

    async with TextSession(
        llm=llm,
        node_dependencies=dependencies,
        initial_node=node_record_service_area(),
        call_id="text-test-1",
    ) as session:
        initial = await session.start()
        turn = await session.send_user_turn(SERVICE_AREA_TURN)
    assert turn.state["service_area"] == SERVICE_AREA_EXPECTED_STATE
    assert initial.assistant_text
    assert turn.current_node is not None
    assert turn.assistant_text
    assert "record_service_area" in llm.calls[0].tool_names


@pytest.mark.asyncio
async def test_text_session_waits_for_delayed_function_transition():
    llm = NonWaitingScriptedLLMService(
        [
            ScriptedLLMResponse(
                function_call=ScriptedFunctionCall(
                    name="record_service_area",
                    arguments={"location": SERVICE_AREA_TURN},
                )
            )
        ]
    )
    dependencies = NodeDependencies(
        validator=DelayedValidator(),
        sms_service=FakeSMS(),
    )

    async with TextSession(
        llm=llm,
        node_dependencies=dependencies,
        initial_node=node_record_service_area(),
        call_id="text-test-delayed",
    ) as session:
        await session.start()
        turn = await session.send_user_turn(SERVICE_AREA_TURN)

    assert turn.assistant_text


@pytest.mark.asyncio
async def test_text_session_captures_scripted_llm_text():
    llm = ScriptedLLMService([ScriptedLLMResponse(text="Please provide more detail.")])

    async with TextSession(
        llm=llm,
        initial_node=node_record_service_area(),
        call_id="text-test-2",
    ) as session:
        await session.start()
        turn = await session.send_user_turn("I need help")

    assert turn.assistant_text[-1] == "Please provide more detail."
    assert llm.calls[0].messages


@pytest.mark.asyncio
async def test_text_session_handles_language_prompt_and_tts_switch_frames():
    llm = ScriptedLLMService(
        [
            ScriptedLLMResponse(
                function_call=ScriptedFunctionCall(
                    name="record_language",
                    arguments={"language": "English"},
                )
            )
        ]
    )

    async with TextSession(
        llm=llm,
        initial_node=node_record_language(),
        call_id="text-test-3",
    ) as session:
        initial = await session.start()
        turn = await session.send_user_turn("English")

    assert len(initial.assistant_text) == 2
    assert turn.state["language"] == {"language": "English"}
    assert turn.assistant_text
