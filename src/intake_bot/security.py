import base64
import hashlib
import hmac

from .env_var import require_env_var

SECRET_KEY_HEX = require_env_var("WEBSOCKET_SECURITY_TOKEN")
try:
    SECRET_KEY = bytes.fromhex(SECRET_KEY_HEX)
except (ValueError, TypeError):
    raise ValueError("WEBSOCKET_SECURITY_TOKEN must be a valid hex string.")


def generate_websocket_auth_code(call_id: str) -> str:
    """
    Generates a secure authentication code for a WebSocket connection based on the provided call ID.

    Args:
        call_id (str): The unique identifier for the call session.

    Returns:
        str: A URL-safe, base64-encoded authentication code derived from the call ID.

    Note:
        This function uses HMAC with SHA-256 and a secret key to generate the authentication code.
    """
    call_sid_bytes = str(call_id).encode("utf-8")
    digest = hmac.new(SECRET_KEY, call_sid_bytes, hashlib.sha256).digest()
    code = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return code


def verify_websocket_auth_code(call_id: str, received_code: str) -> bool:
    """
    Verifies that the provided authentication code matches the expected code for a given call ID.

    Uses constant-time comparison to prevent timing attacks.

    Args:
        call_id (str): The unique identifier for the call/session.
        received_code (str): The authentication code received from the client.

    Returns:
        bool: True if the received code is valid for the given call ID, False otherwise.
    """
    if not (call_id and received_code):
        return False
    expected_code = generate_websocket_auth_code(call_id)
    is_valid = hmac.compare_digest(expected_code, received_code)
    return is_valid
