import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from loguru import logger

from intake_bot.utils.ev import ev_is_true, get_ev

# Suppress noisy pipecat DEBUG logs from turn-detection internals.
_NOISY_PIPECAT_MODULES = {
    "pipecat.turns.user_start",
    "pipecat.audio.turn.smart_turn",
}


def runtime_log_filter(record: dict[str, Any]) -> bool:
    if record["level"].name == "DEBUG":
        name = record["name"] or ""
        for prefix in _NOISY_PIPECAT_MODULES:
            if name.startswith(prefix):
                return False
    return True


def transcript_log_path(call_id: str) -> str:
    return f"logs/{call_id}_transcript.log"


def server_log_path(call_id: str) -> str:
    return f"logs/{call_id}_server.log"


def call_log_filter(call_id: str) -> Callable[[dict[str, Any]], bool]:
    def _filter(record: dict[str, Any]) -> bool:
        return runtime_log_filter(record) and record["extra"].get("call_id") == call_id

    return _filter


@contextmanager
def call_logging_context(call_id: str) -> Iterator[str | None]:
    with logger.contextualize(call_id=call_id):
        if not ev_is_true("LOG_TO_FILE"):
            yield None
            return

        os.makedirs("logs", exist_ok=True)
        log_path = server_log_path(call_id)
        sink_id = logger.add(
            log_path,
            level=get_ev("LOG_LEVEL", "INFO"),
            filter=call_log_filter(call_id),
        )
        try:
            logger.info(f"""Logging server output to file: {log_path}""")
            yield log_path
        finally:
            logger.remove(sink_id)
