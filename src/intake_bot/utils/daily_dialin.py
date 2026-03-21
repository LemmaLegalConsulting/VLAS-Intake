from __future__ import annotations

from typing import Any

from intake_bot.utils.ev import get_ev, require_ev

DEFAULT_DAILY_API_URL = "https://api.daily.co/v1"
DIALIN_HINT_KEYS = {
    "dialin_settings",
    "call_id",
    "callId",
    "call_domain",
    "callDomain",
    "From",
    "from",
    "To",
    "to",
    "daily_api_key",
    "dailyApiKey",
    "daily_api_url",
    "dailyApiUrl",
}


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _normalize_dialin_settings(payload: dict[str, Any]) -> dict[str, Any] | None:
    if isinstance(payload.get("dialin_settings"), dict):
        dialin_settings = payload["dialin_settings"]
    elif isinstance(payload.get("body"), dict) and isinstance(
        payload["body"].get("dialin_settings"), dict
    ):
        dialin_settings = payload["body"]["dialin_settings"]
    else:
        call_id = _first_non_empty(payload.get("call_id"), payload.get("callId"))
        call_domain = _first_non_empty(
            payload.get("call_domain"), payload.get("callDomain")
        )
        if not (call_id and call_domain):
            return None
        dialin_settings = {
            "call_id": call_id,
            "call_domain": call_domain,
            "From": _first_non_empty(payload.get("From"), payload.get("from")),
            "To": _first_non_empty(payload.get("To"), payload.get("to")),
        }

    return {
        "call_id": _first_non_empty(
            dialin_settings.get("call_id"), dialin_settings.get("callId")
        ),
        "call_domain": _first_non_empty(
            dialin_settings.get("call_domain"), dialin_settings.get("callDomain")
        ),
        "From": _first_non_empty(
            dialin_settings.get("From"), dialin_settings.get("from")
        ),
        "To": _first_non_empty(dialin_settings.get("To"), dialin_settings.get("to")),
    }


def looks_like_daily_dialin_body(body: object) -> bool:
    if not isinstance(body, dict):
        return False

    if any(key in body for key in DIALIN_HINT_KEYS):
        return True

    nested_body = body.get("body")
    if isinstance(nested_body, dict) and any(
        key in nested_body for key in DIALIN_HINT_KEYS
    ):
        return True

    return False


def normalize_daily_dialin_body(body: object) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ValueError("Daily dial-in body must be a JSON object.")

    dialin_settings = _normalize_dialin_settings(body)
    if (
        not dialin_settings
        or not dialin_settings.get("call_id")
        or not dialin_settings.get("call_domain")
    ):
        raise ValueError(
            "Missing dial-in call metadata. Expected dialin_settings or Daily webhook fields such as callId and callDomain."
        )

    daily_api_key = _first_non_empty(
        body.get("daily_api_key"),
        body.get("dailyApiKey"),
        get_ev("DAILY_API_KEY"),
    )
    if not daily_api_key:
        daily_api_key = require_ev("DAILY_API_KEY")

    daily_api_url = _first_non_empty(
        body.get("daily_api_url"),
        body.get("dailyApiUrl"),
        get_ev("DAILY_API_URL"),
        DEFAULT_DAILY_API_URL,
    )

    return {
        "dialin_settings": dialin_settings,
        "daily_api_key": daily_api_key,
        "daily_api_url": daily_api_url,
    }
