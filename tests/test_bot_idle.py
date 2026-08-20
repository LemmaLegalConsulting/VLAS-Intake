import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import EndFrame, TTSSpeakFrame

from intake_bot.bot import (
    AdaptiveIdleTimeout,
    IdleRetryHandler,
    StateContextFlowManager,
    TranscriptHandler,
)
from intake_bot.utils.node_prompts import NodePrompts


def test_idle_retry_handler_progresses_through_prompts():
    handler = IdleRetryHandler()
    prompts = NodePrompts()

    first = handler.next_frames("English")
    second = handler.next_frames("English")
    third = handler.next_frames("English")

    assert len(first) == 1
    assert isinstance(first[0], TTSSpeakFrame)
    assert first[0].text == prompts.get_spoken_prompt("idle_retry_first", "English")

    assert len(second) == 1
    assert isinstance(second[0], TTSSpeakFrame)
    assert second[0].text == prompts.get_spoken_prompt("idle_retry_second", "English")

    assert len(third) == 2
    assert isinstance(third[0], TTSSpeakFrame)
    assert third[0].text == prompts.get_spoken_prompt("idle_retry_goodbye", "English")
    assert isinstance(third[1], EndFrame)


def test_idle_retry_handler_reset_restarts_sequence():
    handler = IdleRetryHandler()
    prompts = NodePrompts()

    handler.next_frames("English")
    handler.reset()

    frames = handler.next_frames("English")

    assert len(frames) == 1
    assert isinstance(frames[0], TTSSpeakFrame)
    assert frames[0].text == prompts.get_spoken_prompt("idle_retry_first", "English")


def test_idle_retry_handler_uses_spanish_goodbye():
    handler = IdleRetryHandler()
    prompts = NodePrompts()

    handler.next_frames("Spanish")
    handler.next_frames("Spanish")
    frames = handler.next_frames("Spanish")

    assert len(frames) == 2
    assert isinstance(frames[0], TTSSpeakFrame)
    assert frames[0].text == prompts.get_spoken_prompt("idle_retry_goodbye", "Spanish")
    assert isinstance(frames[1], EndFrame)


def test_adaptive_idle_timeout_keeps_base_for_short_turns():
    policy = AdaptiveIdleTimeout(
        base_timeout_secs=15.0,
        max_timeout_secs=25.0,
        words_per_extra_second=12.0,
    )

    assert policy.timeout_for_content("Yes.") == 15.0


def test_adaptive_idle_timeout_extends_for_long_turns():
    policy = AdaptiveIdleTimeout(
        base_timeout_secs=15.0,
        max_timeout_secs=25.0,
        words_per_extra_second=12.0,
    )

    long_prompt = " ".join(["word"] * 60)

    assert policy.timeout_for_content(long_prompt) == 20.0


def test_adaptive_idle_timeout_respects_maximum():
    policy = AdaptiveIdleTimeout(
        base_timeout_secs=15.0,
        max_timeout_secs=25.0,
        words_per_extra_second=12.0,
    )

    very_long_prompt = " ".join(["word"] * 240)

    assert policy.timeout_for_content(very_long_prompt) == 25.0


@pytest.mark.asyncio
async def test_transcript_handler_ignores_blank_assistant_turn():
    handler = TranscriptHandler()
    handler.save_transcript_message = AsyncMock()

    await handler.on_assistant_transcript(
        None,
        SimpleNamespace(content="   ", timestamp="2026-05-20T22:10:00.000+00:00"),
    )

    handler.save_transcript_message.assert_not_awaited()


def test_state_context_flow_manager_builds_trimmed_state_message():
    flow_manager = object.__new__(StateContextFlowManager)
    flow_manager._state = {
        "call_id": "abc123",
        "tts_voice": "aura-2-mars-en",
        "language": {"language": "English"},
        "domestic_violence": {"is_experiencing": False},
        "income": {"monthly_amount": 0, "listing": {}, "notes": ""},
        "assets": {"listing": [], "receives_benefits": False},
        "_internal": {"skip": True},
    }

    message = flow_manager._build_state_context_message()

    assert message is not None
    assert message["role"] == "developer"
    assert "Caller data collected so far." in message["content"]

    payload = json.loads(message["content"].splitlines()[-1])
    assert payload == {
        "language": {"language": "English"},
        "domestic_violence": {"is_experiencing": False},
        "income": {"monthly_amount": 0},
        "assets": {"receives_benefits": False},
    }
