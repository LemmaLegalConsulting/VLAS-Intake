"""Keep Pipecat 1.7 speech services synchronized when changing languages."""
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterruptionFrame,
    PipelineFlushFrame,
)
from pipecat.pipeline.service_switcher import ServiceSwitcher
from pipecat.processors.frame_processor import FrameDirection


class SpeechServiceSwitcher(ServiceSwitcher):
    async def process_frame(self, frame, direction):
        if isinstance(frame, PipelineFlushFrame) and direction == FrameDirection.UPSTREAM:
            # ParallelPipeline deduplicates by frame ID, including the returning
            # flush probe. Forward its return so the worker can release the wait.
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, (BotStartedSpeakingFrame, BotStoppedSpeakingFrame, InterruptionFrame)):
            # An inactive TTS service may still be paused after its last utterance.
            # It must see playback completion/interruption even after switching,
            # or its next FunctionActionFrame can wait forever on reactivation.
            for service in self.services:
                if service is not self.strategy.active_service:
                    await service.queue_frame(frame, direction)
        await super().process_frame(frame, direction)
