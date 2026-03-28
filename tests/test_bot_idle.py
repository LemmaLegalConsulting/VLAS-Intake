from intake_bot.bot import AdaptiveIdleTimeout, IdleRetryHandler
from pipecat.frames.frames import EndFrame, TTSSpeakFrame


def test_idle_retry_handler_progresses_through_prompts():
    handler = IdleRetryHandler()

    first = handler.next_frames("English")
    second = handler.next_frames("English")
    third = handler.next_frames("English")

    assert len(first) == 1
    assert isinstance(first[0], TTSSpeakFrame)
    assert first[0].text == "Are you still there?"

    assert len(second) == 1
    assert isinstance(second[0], TTSSpeakFrame)
    assert second[0].text == "Would you like to continue with the interview?"

    assert len(third) == 2
    assert isinstance(third[0], TTSSpeakFrame)
    assert third[0].text == (
        "It seems like you're busy right now. Feel free to call back. Have a nice day!"
    )
    assert isinstance(third[1], EndFrame)


def test_idle_retry_handler_reset_restarts_sequence():
    handler = IdleRetryHandler()

    handler.next_frames("English")
    handler.reset()

    frames = handler.next_frames("English")

    assert len(frames) == 1
    assert isinstance(frames[0], TTSSpeakFrame)
    assert frames[0].text == "Are you still there?"


def test_idle_retry_handler_uses_spanish_goodbye():
    handler = IdleRetryHandler()

    handler.next_frames("Spanish")
    handler.next_frames("Spanish")
    frames = handler.next_frames("Spanish")

    assert len(frames) == 2
    assert isinstance(frames[0], TTSSpeakFrame)
    assert frames[0].text == (
        "Parece que está ocupado en este momento. No dude en volver a llamar. ¡Que tenga un buen día!"
    )
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
