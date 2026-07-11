import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService


CLIENT_PYTHON_DIR = Path(__file__).parents[1] / "client" / "python"
sys.path.insert(0, str(CLIENT_PYTHON_DIR))
try:
    from client import InterimTranscriptionFinalizer
finally:
    sys.path.remove(str(CLIENT_PYTHON_DIR))


class CapturingFinalizer(InterimTranscriptionFinalizer):
    def __init__(self, timeout: float):
        super().__init__(timeout=timeout)
        self.frames = []

    async def push_frame(
        self, frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ):
        self.frames.append((frame, direction))


@pytest.mark.asyncio
async def test_interim_flux_transcription_is_finalized_after_quiet_period():
    finalizer = CapturingFinalizer(timeout=0.01)
    interim = InterimTranscriptionFrame(
        text="I need help",
        user_id="caller",
        timestamp="2026-07-10T00:00:00Z",
    )

    await finalizer.process_frame(interim, FrameDirection.DOWNSTREAM)
    await asyncio.sleep(0.03)

    assert [type(frame) for frame, _ in finalizer.frames] == [
        InterimTranscriptionFrame,
        TranscriptionFrame,
    ]
    promoted = finalizer.frames[-1][0]
    assert promoted.text == interim.text
    assert promoted.user_id == interim.user_id
    assert promoted.timestamp == interim.timestamp


@pytest.mark.asyncio
async def test_real_flux_final_cancels_interim_fallback():
    finalizer = CapturingFinalizer(timeout=0.02)
    interim = InterimTranscriptionFrame(
        text="yes",
        user_id="caller",
        timestamp="2026-07-10T00:00:00Z",
    )
    final = TranscriptionFrame(
        text="yes",
        user_id="caller",
        timestamp="2026-07-10T00:00:01Z",
    )

    await finalizer.process_frame(interim, FrameDirection.DOWNSTREAM)
    await asyncio.sleep(0.005)
    await finalizer.process_frame(final, FrameDirection.DOWNSTREAM)
    await asyncio.sleep(0.03)

    assert [frame for frame, _ in finalizer.frames] == [interim, final]


@pytest.mark.asyncio
async def test_flux_start_of_turn_interrupts_bot_for_barge_in():
    stt = DeepgramFluxSTTService(api_key="test")
    stt.broadcast_frame = AsyncMock()
    stt.broadcast_interruption = AsyncMock()
    stt.start_metrics = AsyncMock()

    await stt._handle_start_of_turn("hello")

    stt.broadcast_frame.assert_awaited_once_with(UserStartedSpeakingFrame)
    stt.broadcast_interruption.assert_awaited_once_with()
    assert stt._user_is_speaking is True
