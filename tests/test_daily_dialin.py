import pytest
from intake_bot.utils.daily_dialin import (
    DEFAULT_DAILY_API_URL,
    looks_like_daily_dialin_body,
    normalize_daily_dialin_body,
)


def test_looks_like_daily_dialin_body_accepts_daily_shapes():
    assert looks_like_daily_dialin_body(
        {"callId": "call-123", "callDomain": "domain-123"}
    )
    assert looks_like_daily_dialin_body(
        {"body": {"dialin_settings": {"call_id": "call-123"}}}
    )


def test_looks_like_daily_dialin_body_rejects_sandbox_body():
    assert not looks_like_daily_dialin_body({})
    assert not looks_like_daily_dialin_body({"foo": "bar"})


def test_normalize_daily_dialin_body_accepts_full_runner_payload(monkeypatch):
    monkeypatch.delenv("DAILY_API_KEY", raising=False)

    payload = {
        "daily_api_key": "daily-key",
        "daily_api_url": "https://api.daily.example/v1",
        "dialin_settings": {
            "call_id": "call-123",
            "call_domain": "domain-123",
            "From": "+15551234567",
            "To": "+15557654321",
        },
    }

    normalized = normalize_daily_dialin_body(payload)

    assert normalized == payload


def test_normalize_daily_dialin_body_accepts_raw_daily_webhook_shape(monkeypatch):
    monkeypatch.setenv("DAILY_API_KEY", "env-daily-key")

    normalized = normalize_daily_dialin_body(
        {
            "From": "+15551234567",
            "To": "+15557654321",
            "callId": "call-123",
            "callDomain": "domain-123",
        }
    )

    assert normalized == {
        "daily_api_key": "env-daily-key",
        "daily_api_url": DEFAULT_DAILY_API_URL,
        "dialin_settings": {
            "call_id": "call-123",
            "call_domain": "domain-123",
            "From": "+15551234567",
            "To": "+15557654321",
        },
    }


def test_normalize_daily_dialin_body_accepts_nested_custom_webhook_shape(monkeypatch):
    monkeypatch.setenv("DAILY_API_KEY", "env-daily-key")

    normalized = normalize_daily_dialin_body(
        {
            "body": {
                "dialin_settings": {
                    "from": "+15551234567",
                    "to": "+15557654321",
                    "call_id": "call-123",
                    "call_domain": "domain-123",
                }
            }
        }
    )

    assert normalized == {
        "daily_api_key": "env-daily-key",
        "daily_api_url": DEFAULT_DAILY_API_URL,
        "dialin_settings": {
            "call_id": "call-123",
            "call_domain": "domain-123",
            "From": "+15551234567",
            "To": "+15557654321",
        },
    }


def test_normalize_daily_dialin_body_rejects_missing_call_metadata(monkeypatch):
    monkeypatch.setenv("DAILY_API_KEY", "env-daily-key")

    with pytest.raises(ValueError, match="Missing dial-in call metadata"):
        normalize_daily_dialin_body({})
