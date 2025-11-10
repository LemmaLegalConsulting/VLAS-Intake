from pathlib import Path

from dotenv import load_dotenv
from intake_bot.utils.ev import get_ev

load_dotenv(override=True)


DEBUG = get_ev("LOG_LEVEL") == "DEBUG"

ROOT_DIR = Path(__file__).parent.parent.resolve()

DATA_DIR = Path(ROOT_DIR) / "data"
