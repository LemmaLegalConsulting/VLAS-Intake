import pytest
from intake_bot.utils.daily_dialin import (
    looks_like_daily_dialin_body,
    normalize_daily_dialin_body,
)

_VALID_PAYLOAD = {
    "daily_api_key": "daily-key",
    "daily_api_url": "https://api.daily.example/v1",
    "dialin_settings": {
        "call_id": "call-123",
        "call_domain": "domain-123",
        "From": "+15551234567",
        "To": "+15557654321",
    },
}


class TestLooksLikeDailyDialinBody:
    def test_accepts_official_contract(self):
        assert looks_like_daily_dialin_body(
            {"dialin_settings": {"call_id": "call-123"}}
        )
        assert looks_like_daily_dialin_body({"dialin_settings": {}})

    def test_accepts_any_official_key(self):
        """Any official Daily key triggers detection, even alone."""
        assert looks_like_daily_dialin_body({"daily_api_key": "key"})
        assert looks_like_daily_dialin_body({"daily_api_url": "url"})

    def test_rejects_sandbox_body(self):
        assert not looks_like_daily_dialin_body({})
        assert not looks_like_daily_dialin_body({"foo": "bar"})

    def test_rejects_non_dict(self):
        assert not looks_like_daily_dialin_body("not-a-dict")
        assert not looks_like_daily_dialin_body(None)
        assert not looks_like_daily_dialin_body([])


class TestNormalizeValid:
    def test_accepts_full_official_payload(self):
        normalized = normalize_daily_dialin_body(_VALID_PAYLOAD)
        assert normalized == _VALID_PAYLOAD

    def test_from_and_to_are_optional(self):
        minimal = {
            "daily_api_key": "k",
            "daily_api_url": "https://api.daily.co/v1",
            "dialin_settings": {
                "call_id": "c",
                "call_domain": "d",
            },
        }
        normalized = normalize_daily_dialin_body(minimal)
        assert normalized["dialin_settings"]["call_id"] == "c"
        assert normalized["dialin_settings"]["call_domain"] == "d"
        assert normalized["dialin_settings"]["From"] == ""
        assert normalized["dialin_settings"]["To"] == ""


class TestNormalizeMissingRequired:
    def test_rejects_missing_dialin_settings(self):
        with pytest.raises(ValueError, match="Missing dialin_settings"):
            normalize_daily_dialin_body({"daily_api_key": "k", "daily_api_url": "u"})

    def test_rejects_none_dialin_settings(self):
        with pytest.raises(ValueError, match="Missing dialin_settings"):
            normalize_daily_dialin_body(
                {"dialin_settings": None, "daily_api_key": "k", "daily_api_url": "u"}
            )

    def test_rejects_missing_call_id(self):
        with pytest.raises(ValueError, match="call_id"):
            normalize_daily_dialin_body(
                {
                    "daily_api_key": "k",
                    "daily_api_url": "u",
                    "dialin_settings": {"call_domain": "d"},
                }
            )

    def test_rejects_missing_call_domain(self):
        with pytest.raises(ValueError, match="call_domain"):
            normalize_daily_dialin_body(
                {
                    "daily_api_key": "k",
                    "daily_api_url": "u",
                    "dialin_settings": {"call_id": "c"},
                }
            )

    def test_rejects_empty_call_id(self):
        with pytest.raises(ValueError, match="call_id"):
            normalize_daily_dialin_body(
                {
                    "daily_api_key": "k",
                    "daily_api_url": "u",
                    "dialin_settings": {"call_id": "", "call_domain": "d"},
                }
            )

    def test_rejects_missing_daily_api_key(self):
        with pytest.raises(ValueError, match="daily_api_key"):
            normalize_daily_dialin_body(
                {
                    "daily_api_url": "u",
                    "dialin_settings": {"call_id": "c", "call_domain": "d"},
                }
            )

    def test_rejects_missing_daily_api_url(self):
        with pytest.raises(ValueError, match="daily_api_url"):
            normalize_daily_dialin_body(
                {
                    "daily_api_key": "k",
                    "dialin_settings": {"call_id": "c", "call_domain": "d"},
                }
            )


class TestNormalizePartialMetadata:
    """Partial official Daily metadata must be rejected — never treated as sandbox/local input."""

    def test_rejects_missing_both_daily_api_key_and_url(self):
        with pytest.raises(ValueError, match="daily_api_key"):
            normalize_daily_dialin_body(
                {
                    "dialin_settings": {"call_id": "c", "call_domain": "d"},
                }
            )

    def test_rejects_empty_daily_api_key(self):
        with pytest.raises(ValueError, match="daily_api_key"):
            normalize_daily_dialin_body(
                {
                    "daily_api_key": "",
                    "daily_api_url": "u",
                    "dialin_settings": {"call_id": "c", "call_domain": "d"},
                }
            )

    def test_rejects_empty_daily_api_url(self):
        with pytest.raises(ValueError, match="daily_api_url"):
            normalize_daily_dialin_body(
                {
                    "daily_api_key": "k",
                    "daily_api_url": "",
                    "dialin_settings": {"call_id": "c", "call_domain": "d"},
                }
            )


class TestNormalizeMalformedTypes:
    def test_rejects_non_dict_body(self):
        with pytest.raises(
            ValueError, match="Daily dial-in body must be a JSON object"
        ):
            normalize_daily_dialin_body("this-is-a-string")

    def test_rejects_list_body(self):
        with pytest.raises(
            ValueError, match="Daily dial-in body must be a JSON object"
        ):
            normalize_daily_dialin_body([])

    def test_rejects_none_body(self):
        with pytest.raises(
            ValueError, match="Daily dial-in body must be a JSON object"
        ):
            normalize_daily_dialin_body(None)

    def test_rejects_int_body(self):
        with pytest.raises(
            ValueError, match="Daily dial-in body must be a JSON object"
        ):
            normalize_daily_dialin_body(42)

    def test_rejects_numeric_daily_api_key(self):
        with pytest.raises(ValueError, match="daily_api_key"):
            normalize_daily_dialin_body(
                {
                    "daily_api_key": 123,
                    "daily_api_url": "u",
                    "dialin_settings": {"call_id": "c", "call_domain": "d"},
                }
            )

    def test_rejects_list_daily_api_url(self):
        with pytest.raises(ValueError, match="daily_api_url"):
            normalize_daily_dialin_body(
                {
                    "daily_api_key": "k",
                    "daily_api_url": ["not-a-string"],
                    "dialin_settings": {"call_id": "c", "call_domain": "d"},
                }
            )

    def test_rejects_dict_daily_api_key(self):
        with pytest.raises(ValueError, match="daily_api_key"):
            normalize_daily_dialin_body(
                {
                    "daily_api_key": {},
                    "daily_api_url": "u",
                    "dialin_settings": {"call_id": "c", "call_domain": "d"},
                }
            )

    def test_rejects_bool_daily_api_url(self):
        with pytest.raises(ValueError, match="daily_api_url"):
            normalize_daily_dialin_body(
                {
                    "daily_api_key": "k",
                    "daily_api_url": True,
                    "dialin_settings": {"call_id": "c", "call_domain": "d"},
                }
            )


class TestSandboxSeparation:
    """An empty or non-Daily body must not be forced through normalize (sandbox path
    in bot.py uses looks_like_daily_dialin_body first)."""

    def test_empty_body_is_not_daily(self):
        assert not looks_like_daily_dialin_body({})

    def test_sandbox_body_is_not_daily(self):
        assert not looks_like_daily_dialin_body({"some": "data"})

    def test_daily_api_key_alone_is_now_daily(self):
        """daily_api_key alone is now recognised as an official Daily key."""
        assert looks_like_daily_dialin_body({"daily_api_key": "key"})

    def test_daily_api_url_alone_is_now_daily(self):
        assert looks_like_daily_dialin_body({"daily_api_url": "https://example.com"})

    def test_empty_dialin_settings_is_still_daily(self):
        assert looks_like_daily_dialin_body({"dialin_settings": {}})


# ---------------------------------------------------------------------------
# Production-route bot() tests (mocked DailyTransport / run_bot)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_daily_transport(monkeypatch):
    """Prevent real DailyTransport / run_bot from being instantiated."""
    import intake_bot.bot as bot_mod

    captured = {"transport_calls": [], "run_bot_calls": []}

    async def fake_run_bot(
        transport,
        call_id,
        caller_phone_number,
        handle_sigint,
        configure_transport=None,
        user_idle_timeout_secs=None,
        strict_user_muting=False,
    ):
        captured["run_bot_calls"].append(
            {
                "call_id": call_id,
                "caller_phone_number": caller_phone_number,
            }
        )

    monkeypatch.setattr(bot_mod, "run_bot", fake_run_bot)

    # Mock DailyTransport construction to avoid real Daily imports
    class FakeDailyTransport:
        def __init__(self, *args, **kwargs):
            captured["transport_calls"].append({"args": args, "kwargs": kwargs})

        def input(self):
            return None

        def output(self):
            return None

        def event_handler(self, event):
            def decorator(fn):
                return fn

            return decorator

    monkeypatch.setattr(bot_mod, "DailyTransport", FakeDailyTransport)
    return captured


_FAKE_RUNNER_ARGS = type(
    "RunnerArgs",
    (),
    {
        "body": None,
        "room_url": "https://example.daily.co/room",
        "token": "fake-token",
        "handle_sigint": False,
    },
)()


@pytest.mark.asyncio
async def test_bot_takes_daily_path_for_complete_official_request(mock_daily_transport):
    """Complete valid DailyDialinRequest takes the Daily dial-in path."""
    _FAKE_RUNNER_ARGS.body = {
        "daily_api_key": "k-test",
        "daily_api_url": "https://api.daily.co/v1",
        "dialin_settings": {
            "call_id": "call-abc",
            "call_domain": "dom-xyz",
        },
    }
    from intake_bot.bot import bot

    await bot(_FAKE_RUNNER_ARGS)
    calls = mock_daily_transport["run_bot_calls"]
    assert len(calls) == 1, f"Expected 1 run_bot call, got {len(calls)}"
    assert calls[0]["call_id"] == "call-abc"
    # Caller phone number should be empty when From is absent
    assert calls[0]["caller_phone_number"] == ""


@pytest.mark.asyncio
async def test_bot_takes_sandbox_path_for_empty_body(mock_daily_transport):
    """Genuine sandbox body with no official keys uses sandbox path."""
    _FAKE_RUNNER_ARGS.body = {}
    from intake_bot.bot import bot

    await bot(_FAKE_RUNNER_ARGS)
    calls = mock_daily_transport["run_bot_calls"]
    assert len(calls) == 1
    assert calls[0]["call_id"] == "sandbox-session"
    assert calls[0]["caller_phone_number"] == ""


def _capture_loguru_logs():
    """Return (sink_id, list) to capture loguru messages."""
    from loguru import logger

    captured = []
    sink_id = logger.add(lambda msg: captured.append(msg), level="INFO")
    return sink_id, captured


@pytest.mark.asyncio
async def test_bot_rejects_partial_official_missing_daily_api_key(mock_daily_transport):
    """Partial official payload (dialin_settings present, no daily_api_key)
    is rejected before reaching run_bot."""
    from loguru import logger

    _FAKE_RUNNER_ARGS.body = {
        "dialin_settings": {"call_id": "c", "call_domain": "d"},
    }
    from intake_bot.bot import bot

    sink_id, captured = _capture_loguru_logs()
    try:
        await bot(_FAKE_RUNNER_ARGS)
    finally:
        logger.remove(sink_id)

    # Should NOT have called run_bot (neither daily nor sandbox)
    assert len(mock_daily_transport["run_bot_calls"]) == 0
    # Should have logged a safe error
    safe_phrases = ["Invalid Daily dial-in request", "DailyDialinRequest"]
    assert any(any(phrase in msg for phrase in safe_phrases) for msg in captured), (
        f"No safe error logged. Got: {captured}"
    )
    # Ensure no raw credential values leaked
    for msg in captured:
        assert "k-test" not in msg
        assert "daily-key" not in msg


@pytest.mark.asyncio
async def test_bot_rejects_malformed_daily_api_url_type(mock_daily_transport):
    """Malformed daily_api_url (list) is rejected before transport creation."""
    from loguru import logger

    _FAKE_RUNNER_ARGS.body = {
        "daily_api_key": "k",
        "daily_api_url": ["not-a-string"],
        "dialin_settings": {"call_id": "c", "call_domain": "d"},
    }
    from intake_bot.bot import bot

    sink_id, captured = _capture_loguru_logs()
    try:
        await bot(_FAKE_RUNNER_ARGS)
    finally:
        logger.remove(sink_id)

    assert len(mock_daily_transport["run_bot_calls"]) == 0
    assert any("Invalid Daily dial-in request" in msg for msg in captured)


@pytest.mark.asyncio
async def test_bot_rejects_missing_dialin_settings_with_creds(mock_daily_transport):
    """daily_api_key+daily_api_url alone (no dialin_settings) is recognised as
    official but fails validation."""
    from loguru import logger

    _FAKE_RUNNER_ARGS.body = {
        "daily_api_key": "k",
        "daily_api_url": "https://api.daily.co/v1",
    }
    from intake_bot.bot import bot

    sink_id, captured = _capture_loguru_logs()
    try:
        await bot(_FAKE_RUNNER_ARGS)
    finally:
        logger.remove(sink_id)

    assert len(mock_daily_transport["run_bot_calls"]) == 0
    assert any("Invalid Daily dial-in request" in msg for msg in captured)


@pytest.mark.asyncio
async def test_bot_sandbox_still_works_after_daily_detection_change(
    mock_daily_transport,
):
    """A body with no official keys still takes the sandbox path."""
    _FAKE_RUNNER_ARGS.body = {"unrelated": "data"}
    from intake_bot.bot import bot

    await bot(_FAKE_RUNNER_ARGS)
    assert len(mock_daily_transport["run_bot_calls"]) == 1
    assert mock_daily_transport["run_bot_calls"][0]["call_id"] == "sandbox-session"


# ---------------------------------------------------------------------------
# Documentation consistency tests
# ---------------------------------------------------------------------------


def _readme_text(path: str) -> str:
    import os as _os

    repo = _os.path.join(_os.path.dirname(__file__), "..")
    with open(_os.path.join(repo, path), encoding="utf-8") as _f:
        return _f.read()


class TestReadmeNoObsoletePhrases:
    """Scan README files for forbidden obsolete phrases."""

    README_PATHS = [
        "README.md",
        "client/python/README.md",
        "client/typescript/README.md",
    ]

    FORBIDDEN_PATTERNS: list[tuple[str, str]] = [
        (
            "query string",
            "Should describe first-frame JSON metadata, not query-string params",
        ),
        ("query metadata", "Should describe first-frame JSON, not query metadata"),
        ("ngrok.*daily", "Daily dial-in ngrok workflow is removed (case-insensitive)"),
        ("custom.*webhook", "Custom webhook compatibility is removed"),
        ("probe_daily", "Probe script is deleted"),
        ("local.*daily.*dial", "Local Daily dial-in workflow is removed"),
    ]

    def test_no_forbidden_phrases(self):
        import re as _re

        for path in self.README_PATHS:
            text = _readme_text(path).lower()
            for pattern, reason in self.FORBIDDEN_PATTERNS:
                pattern_lower = pattern.lower()
                if _re.search(pattern_lower.replace("\\", "\\\\"), text):
                    pytest.fail(
                        f"{path}: forbidden pattern '{pattern}' found. {reason}"
                    )


class TestReadmeProtocolClaims:
    """Verify key protocol claims in documentation match implementation."""

    def test_python_client_mentions_first_frame_json(self):
        text = _readme_text("client/python/README.md")
        assert "first-frame JSON metadata" in text, (
            "Python client README must mention first-frame JSON metadata"
        )

    def test_typescript_client_mentions_first_frame_json(self):
        text = _readme_text("client/typescript/README.md")
        assert "first-frame JSON metadata" in text, (
            "TypeScript client README must mention first-frame JSON metadata"
        )

    def test_python_client_mentions_45s_timeout(self):
        text = _readme_text("client/python/README.md")
        assert "45" in text, (
            "Python client README must mention 45-second ws-test timeout"
        )

    def test_python_client_no_query_string(self):
        text = _readme_text("client/python/README.md")
        assert "/ws?" not in text, (
            "Python client README must not reference query-string connection"
        )

    def test_root_readme_no_local_daily_pstn(self):
        text = _readme_text("README.md")
        assert "bot.py -t daily --dialin" not in text, (
            "Root README must not contain local Daily PSTN dial-in instructions"
        )
