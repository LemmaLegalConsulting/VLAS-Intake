from pipecat.frames.frames import TranscriptionFrame
from pipecat.turns.user_stop.external_user_turn_stop_strategy import (
    ExternalUserTurnStopStrategy,
)


class DeduplicatingExternalUserTurnStopStrategy(ExternalUserTurnStopStrategy):
    """Avoid repeated inference triggers for the same buffered transcript.

    The external stop strategy can retry its timeout check while transcript
    state remains buffered. This variant only re-arms after new final
    transcription text arrives.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._has_unprocessed_text = False
        self._stop_triggered = False
        self._external_stop_received = False

    async def handle_user_turn_started(self):
        await super().handle_user_turn_started()
        self._has_unprocessed_text = False
        self._stop_triggered = False
        self._external_stop_received = False

    async def handle_user_turn_stopped(self):
        await super().handle_user_turn_stopped()
        self._has_unprocessed_text = False

    async def _handle_user_started_speaking(self, _):
        self._stop_triggered = False
        self._external_stop_received = False
        await super()._handle_user_started_speaking(_)

    async def _handle_user_stopped_speaking(self, _):
        self._external_stop_received = True
        await super()._handle_user_stopped_speaking(_)

    async def _handle_transcription(self, frame: TranscriptionFrame):
        await super()._handle_transcription(frame)
        self._has_unprocessed_text = True

    async def _maybe_trigger_user_turn_stopped(self):
        if self._stop_triggered:
            return

        if not self._wait_for_transcript:
            if not self._external_stop_received or self._user_speaking:
                return
            self._stop_triggered = True
            await super()._maybe_trigger_user_turn_stopped()
            return

        if not self._has_unprocessed_text:
            return

        if not self._user_speaking and not self._seen_interim_results and self._text:
            self._stop_triggered = True
            self._has_unprocessed_text = False
            await self.trigger_user_turn_stopped()
