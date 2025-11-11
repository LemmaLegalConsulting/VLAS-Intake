from pathlib import Path

from dotenv import load_dotenv
from intake_bot.utils.ev import get_ev

load_dotenv(override=True)


DEBUG = get_ev("LOG_LEVEL") == "DEBUG"

APPLICATION_ROOT = Path(__file__).parent.parent.resolve()

DATA_DIR = Path(APPLICATION_ROOT) / "data"

PROJECT_ROOT = Path(APPLICATION_ROOT).parent.parent
