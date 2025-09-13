import pytest
from src.intake_bot.security import generate_websocket_auth_code, verify_websocket_auth_code


@pytest.fixture
def mock_secret_key(monkeypatch):
    # Mock the environment variable for the secret key
    test_key_hex = "a1b2c3d4e5f678901234567890abcdef1234567890abcdef1234567890abcdef"  # Example 64-char hex
    monkeypatch.setenv("WEBSOCKET_SECURITY_TOKEN", test_key_hex)


def test_generate_websocket_auth_code(mock_secret_key):
    call_id = "test-call-sid-123"
    code = generate_websocket_auth_code(call_id)
    assert isinstance(code, str)
    assert len(code) > 0  # Code should not be empty


def test_verify_websocket_auth_code_valid(mock_secret_key):
    call_id = "test-call-sid-123"
    code = generate_websocket_auth_code(call_id)
    assert verify_websocket_auth_code(call_id, code) is True


def test_verify_websocket_auth_code_invalid_call_id(mock_secret_key):
    call_id = "test-call-sid-123"
    wrong_call_id = "wrong-call-sid-456"
    code = generate_websocket_auth_code(call_id)
    assert verify_websocket_auth_code(wrong_call_id, code) is False


def test_verify_websocket_auth_code_tampered_code(mock_secret_key):
    call_id = "test-call-sid-123"
    code = generate_websocket_auth_code(call_id)
    tampered_code = code[:-1] + "x"  # Tamper the last character
    assert verify_websocket_auth_code(call_id, tampered_code) is False


def test_verify_websocket_auth_code_empty_inputs(mock_secret_key):
    assert verify_websocket_auth_code("", "") is False
    assert verify_websocket_auth_code("valid", "") is False
    assert verify_websocket_auth_code("", "valid") is False
