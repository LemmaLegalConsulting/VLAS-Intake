import sys

from intake_bot.server import logger
from intake_bot.utils.ev import ev_is_true

if ev_is_true("DISABLE_LOCAL_SMART_TURN"):
    logger.info("LocalSmartTurnAnalyzerV3 is not enabled.")
    turn_analyzer = None
else:
    try:
        from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3

        turn_analyzer = LocalSmartTurnAnalyzerV3()
    except Exception:
        sys.exit(
            "[INFO] intake-bot: You are missing the module 'LocalSmartTurnAnalyzerV3'. You use `uv sync` to install the module."
        )
