import asyncio

import pytest
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame, ManuallySwitchServiceFrame, PipelineFlushFrame,
    StartFrame, TextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

from intake_bot.speech_switcher import SpeechServiceSwitcher


class PausedSpeechService(FrameProcessor):
    def __init__(self, *, pause=False):
        super().__init__()
        self.pause = pause
        self.ready = asyncio.Event()
        self.received = asyncio.Event()

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            if self.pause:
                await self.pause_processing_frames()
            self.ready.set()
        if isinstance(frame, BotStoppedSpeakingFrame):
            await self.resume_processing_frames()
        if isinstance(frame, TextFrame):
            self.received.set()
        await self.push_frame(frame, direction)


@pytest.mark.asyncio
async def test_inactive_voice_resumes_and_flush_probe_returns():
    english = PausedSpeechService()
    spanish = PausedSpeechService(pause=True)
    switcher = SpeechServiceSwitcher(services=[english, spanish])
    worker = PipelineWorker(Pipeline([switcher]), enable_rtvi=False, idle_timeout_secs=None)
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    task = asyncio.create_task(runner.run())
    try:
        await asyncio.wait_for(spanish.ready.wait(), 2)
        # Playback finishes after this voice became inactive. Its queue must resume.
        await worker.queue_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        await worker.queue_frame(ManuallySwitchServiceFrame(service=spanish))
        done = asyncio.Event()
        await worker.queue_frames([TextFrame('Spanish response'), PipelineFlushFrame(event=done)])
        await asyncio.wait_for(done.wait(), 2)
        assert spanish.received.is_set()
    finally:
        await worker.cancel()
        await asyncio.wait_for(task, 2)
