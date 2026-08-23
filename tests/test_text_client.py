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
        _scenario_phone_number,
        run_text_scenario,
    )
finally:
    sys.path.remove(str(CLIENT_PYTHON_DIR))


def test_scenario_loader_reads_expected_state_and_prompt():
    scripts = load_scripts()

    assert "celeste" in scripts
    assert "expected_state" in scripts["celeste"]
    assert "Scenario:" in build_caller_system_prompt("celeste", scripts)


def test_scenario_phone_number_uses_expected_scenario_phone():
    scripts = load_scripts()

    assert _scenario_phone_number(scripts["angela"], "0000000000") == "(434) 555-0643"
    assert _scenario_phone_number({}, "0000000000") == "0000000000"


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


class EchoThenValidCompletions:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self._responses = iter(["What is your name?", "Taylor Campbell"])

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        content = next(self._responses)
        message = type("Message", (), {"content": content})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice]})()


class EchoThenValidClient:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": EchoThenValidCompletions()})()


@pytest.mark.asyncio
async def test_azure_caller_retries_when_model_echoes_bot_prompt():
    client = EchoThenValidClient()
    caller = AzureCaller(
        client=client,
        model="gpt-4.1-mini",
        system_prompt="Scenario facts",
    )

    reply = await caller.reply(("What is your name?",))

    assert reply == "Taylor Campbell"
    assert len(client.chat.completions.calls) == 2


class QuestionThenValidCompletions:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self._responses = iter(
            ["Now, please provide your date of birth.", "I have no income."]
        )

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        content = next(self._responses)
        message = type("Message", (), {"content": content})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice]})()


class QuestionThenValidClient:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": QuestionThenValidCompletions()})()


@pytest.mark.asyncio
async def test_azure_caller_retries_when_model_returns_bot_prompt():
    client = QuestionThenValidClient()
    caller = AzureCaller(
        client=client,
        model="gpt-4.1-mini",
        system_prompt="Scenario facts",
    )

    reply = await caller.reply(("What income do you have?",))

    assert reply == "I have no income."
    assert len(client.chat.completions.calls) == 2


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
    assert result.assistant_transcript == ("What is your name?", "Thank you. Goodbye.")
