import base64
import hashlib
import hmac
import os

from dotenv import load_dotenv

load_dotenv(override=True)

SECRET_KEY_HEX = os.getenv("WEBSOCKET_SECURITY_TOKEN")
if not SECRET_KEY_HEX:
    raise ValueError("WEBSOCKET_SECURITY_TOKEN environment variable is not set.")
try:
    SECRET_KEY = bytes.fromhex(SECRET_KEY_HEX)
except (ValueError, TypeError):
    raise ValueError("WEBSOCKET_SECURITY_TOKEN must be a valid hex string.")


def generate_websocket_auth_code(call_sid: str) -> str:
    """Generate an HMAC-SHA256 code for the given CallSid, base64-encoded."""
    call_sid_bytes = call_sid.encode("utf-8")
    digest = hmac.new(SECRET_KEY, call_sid_bytes, hashlib.sha256).digest()
    code = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return code


def verify_websocket_auth_code(call_sid: str, received_code: str) -> bool:
    """Verify that the received code matches the expected HMAC for this CallSid."""
    expected_code = generate_websocket_auth_code(call_sid)
    # Use constant-time comparison to prevent timing attacks
    return hmac.compare_digest(expected_code, received_code)
