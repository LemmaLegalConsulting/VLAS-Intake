import asyncio

import pytest
from intake_bot.turn_strategies import DeduplicatingExternalUserTurnStopStrategy
from pipecat.frames.frames import (
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.utils.asyncio.task_manager import TaskManager, TaskManagerParams


@pytest.mark.asyncio
async def test_deduplicating_external_strategy_triggers_once_per_buffered_text():
    task_manager = TaskManager()
    task_manager.setup(TaskManagerParams(loop=asyncio.get_running_loop()))

    strategy = DeduplicatingExternalUserTurnStopStrategy(timeout=0.05)
    await strategy.setup(task_manager)

    stop_count = 0

    @strategy.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(strategy, params):
        nonlocal stop_count
        stop_count += 1

    await strategy.process_frame(UserStartedSpeakingFrame())
    await strategy.process_frame(
        TranscriptionFrame(text="No. I don't.", user_id="caller", timestamp="")
    )
    await strategy.process_frame(UserStoppedSpeakingFrame())

    await asyncio.sleep(0.2)
    assert stop_count == 1

    await strategy.process_frame(UserStartedSpeakingFrame())
    await strategy.process_frame(
        TranscriptionFrame(text=" Hello?", user_id="caller", timestamp="")
    )
    await strategy.process_frame(UserStoppedSpeakingFrame())

    await asyncio.sleep(0.1)
    assert stop_count == 2

    await strategy.cleanup()
