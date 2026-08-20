from __future__ import annotations

from typing import Any

_OFFICIAL_DAILY_KEYS = frozenset(
    {
        "dialin_settings",
        "daily_api_key",
        "daily_api_url",
    }
)


def looks_like_daily_dialin_body(body: object) -> bool:
    if not isinstance(body, dict):
        return False
    return any(key in body for key in _OFFICIAL_DAILY_KEYS)


def _ensure_nonempty_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(  # noqa: TRY004 - public validation API uses ValueError
            f"Required field {field} must be a non-empty string."
        )
    if not value:
        raise ValueError(f"Required field {field} must be a non-empty string.")
    return value


def normalize_daily_dialin_body(body: object) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ValueError(  # noqa: TRY004 - public validation API uses ValueError
            "Daily dial-in body must be a JSON object."
        )

    dialin_settings = body.get("dialin_settings")
    if not isinstance(dialin_settings, dict):
        raise ValueError(  # noqa: TRY004 - public validation API uses ValueError
            "Missing dialin_settings. Use Pipecat Cloud's official DailyDialinRequest format."
        )

    call_id = dialin_settings.get("call_id")
    call_domain = dialin_settings.get("call_domain")
    if not call_id or not call_domain:
        raise ValueError(
            "Missing dial-in call metadata: dialin_settings must include call_id and call_domain."
        )

    daily_api_key = _ensure_nonempty_str(body.get("daily_api_key"), "daily_api_key")
    daily_api_url = _ensure_nonempty_str(body.get("daily_api_url"), "daily_api_url")

    return {
        "dialin_settings": {
            "call_id": call_id,
            "call_domain": call_domain,
            "From": dialin_settings.get("From", ""),
            "To": dialin_settings.get("To", ""),
        },
        "daily_api_key": daily_api_key,
        "daily_api_url": daily_api_url,
    }
