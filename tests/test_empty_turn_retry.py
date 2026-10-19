from pipecat.frames.frames import EndFrame, TTSSpeakFrame

from intake_bot.bot import IdleRetryHandler
from intake_bot.utils.node_prompts import NodePrompts


def test_idle_retry_handlers_have_independent_counters():
    idle_handler = IdleRetryHandler()
    empty_handler = IdleRetryHandler()

    idle_handler.next_frames("English")
    idle_handler.next_frames("English")

    empty_handler.next_frames("English")
    empty_handler.next_frames("English")
    last_empty = empty_handler.next_frames("English")
    assert isinstance(last_empty[0], TTSSpeakFrame)
    assert last_empty[0].text == NodePrompts().get_spoken_prompt(
        "idle_retry_goodbye", "English"
    )


def test_empty_turn_reset_restarts_sequence():
    handler = IdleRetryHandler()

    handler.next_frames("English")
    handler.next_frames("English")
    handler.reset()

    frames = handler.next_frames("English")
    assert len(frames) == 1
    assert isinstance(frames[0], TTSSpeakFrame)
    assert frames[0].text == NodePrompts().get_spoken_prompt(
        "idle_retry_first", "English"
    )


def test_empty_turn_uses_spanish_prompts():
    handler = IdleRetryHandler()

    handler.next_frames("Spanish")
    handler.next_frames("Spanish")
    frames = handler.next_frames("Spanish")

    assert len(frames) == 2
    assert isinstance(frames[0], TTSSpeakFrame)
    assert frames[0].text == NodePrompts().get_spoken_prompt(
        "idle_retry_goodbye", "Spanish"
    )
    assert isinstance(frames[1], EndFrame)


def test_empty_turn_handler_uses_custom_prefix():
    handler = IdleRetryHandler(prompt_prefix="empty_turn_retry")
    frames = handler.next_frames("English")
    assert len(frames) == 1
    assert isinstance(frames[0], TTSSpeakFrame)
    expected = NodePrompts().get_spoken_prompt("empty_turn_retry_first", "English")
    assert frames[0].text == expected


def test_empty_turn_handler_separate_from_idle():
    idle_handler = IdleRetryHandler(prompt_prefix="idle_retry")
    empty_handler = IdleRetryHandler(prompt_prefix="empty_turn_retry")

    frames = empty_handler.next_frames("English")
    assert frames[0].text == NodePrompts().get_spoken_prompt(
        "empty_turn_retry_first", "English"
    )
    # idle prompts should still work independently
    idle_frames = idle_handler.next_frames("English")
    assert idle_frames[0].text == NodePrompts().get_spoken_prompt(
        "idle_retry_first", "English"
    )
