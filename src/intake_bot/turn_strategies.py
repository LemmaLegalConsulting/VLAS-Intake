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

    async def handle_user_turn_started(self):
        await super().handle_user_turn_started()
        self._has_unprocessed_text = False

    async def handle_user_turn_stopped(self):
        await super().handle_user_turn_stopped()
        self._has_unprocessed_text = False

    async def _handle_transcription(self, frame: TranscriptionFrame):
        await super()._handle_transcription(frame)
        self._has_unprocessed_text = True

    async def _maybe_trigger_user_turn_stopped(self):
        if not self._has_unprocessed_text:
            return

        if not self._user_speaking and not self._seen_interim_results and self._text:
            self._has_unprocessed_text = False
            await self.trigger_user_turn_stopped()
