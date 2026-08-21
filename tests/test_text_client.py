import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import pytest

CLIENT_PYTHON_DIR = Path(__file__).parents[1] / "client" / "python"
sys.path.insert(0, str(CLIENT_PYTHON_DIR))
try:
    from scenarios import build_caller_system_prompt, load_scripts
    from text_client import (
        AzureCaller,
        TextScenarioResult,
        run_text_scenario,
    )
finally:
    sys.path.remove(str(CLIENT_PYTHON_DIR))


def test_scenario_loader_reads_expected_state_and_prompt():
    scripts = load_scripts()

    assert "celeste" in scripts
    assert "expected_state" in scripts["celeste"]
    assert "Scenario:" in build_caller_system_prompt("celeste", scripts)


class FakeAzureResponse:
    class Choice:
        class Message:
            content = "My reply"

        message = Message()

    choices: ClassVar[list] = [Choice()]


class FakeChatCompletions:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeAzureResponse()


class FakeAzureClient:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": FakeChatCompletions()})()


@pytest.mark.asyncio
async def test_azure_caller_preserves_scenario_conversation_history():
    client = FakeAzureClient()
    caller = AzureCaller(
        client=client,
        model="gpt-4.1-mini",
        system_prompt="Scenario facts",
    )

    reply = await caller.reply(("What is your name?",))

    assert reply == "My reply"
    request = client.chat.completions.calls[0]
    assert request["model"] == "gpt-4.1-mini"
    assert request["messages"] == [
        {"role": "system", "content": "Scenario facts"},
        {"role": "assistant", "content": "What is your name?"},
    ]


@dataclass
class FakeTurnResult:
    assistant_text: tuple[str, ...]
    state: dict[str, Any]


class FakeTextSession:
    def __init__(self, **kwargs):
        self.finished = False
        self._turns = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return None

    async def start(self):
        return FakeTurnResult(("What is your name?",), {})

    async def send_user_turn(self, text):
        self._turns += 1
        self.finished = True
        return FakeTurnResult(("Thank you. Goodbye.",), {"name": text})


class FakeCaller:
    async def reply(self, assistant_text):
        return "Taylor"


@pytest.mark.asyncio
async def test_run_text_scenario_returns_final_state():
    result = await run_text_scenario(
        "scenario",
        bot_llm=object(),
        caller=FakeCaller(),
        call_id="text-1",
        caller_phone_number="8665345243",
        session_factory=FakeTextSession,
        max_turns=3,
    )

    assert isinstance(result, TextScenarioResult)
    assert result.call_id == "text-1"
    assert result.turn_count == 1
    assert result.state == {"name": "Taylor"}
