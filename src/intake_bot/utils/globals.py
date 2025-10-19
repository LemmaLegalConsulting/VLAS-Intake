from pathlib import Path

from dotenv import load_dotenv
from intake_bot.utils.ev import require_ev

load_dotenv(override=True)


ROOT_DIR = Path(__file__).parent.parent.resolve()

LEGALSERVER_API_BASE_URL = (
    f"""https://{require_ev("LEGAL_SERVER_SUBDOMAIN")}.legalserver.org/api/v2/"""
)

LEGALSERVER_HEADERS = {
    "Authorization": f"""Bearer {require_ev("LEGAL_SERVER_BEARER_TOKEN")}""",
    "Content-Type": "application/json",
    "Accept": "application/json, text/html",
}
