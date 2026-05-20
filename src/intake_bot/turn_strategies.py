from pipecat.frames.frames import TranscriptionFrame
from pipecat.turns.user_stop.external_user_turn_stop_strategy import (
    ExternalUserTurnStopStrategy,
)


class DeduplicatingExternalUserTurnStopStrategy(ExternalUserTurnStopStrategy):
    """Avoid repeated inference triggers for the same buffered transcript.

    Intake-bot uses the external stop strategy together with incomplete-turn
    filtering, which defers semantic finalization until the LLM emits its
    completion signal. If the first LLM attempt stalls or returns malformed tool
    arguments, the base strategy can keep retriggering inference every timeout
    interval for the same buffered text. This variant only re-arms when new
    final transcription text arrives.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._has_unprocessed_text = False

    async def reset(self):
        await super().reset()
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
