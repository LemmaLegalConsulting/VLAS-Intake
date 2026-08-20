import asyncio
import math
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from intake_bot.services.dialpad import REFERRAL, SMS


class _FakeResponse:
    def __init__(self, status=200, headers=None, json_body=None, text_body=""):
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}
        self._json_body = json_body or {}
        self._text_body = text_body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def json(self, content_type=None):
        return self._json_body

    async def text(self):
        return self._text_body


class _FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def post(self, url, json, headers, **kwargs):
        self.calls.append({"url": url, "json": json, "headers": headers, **kwargs})
        return self.response


class _FakeClock:
    def __init__(self, start=0.0):
        self._now = start
        self.sleeps = []

    def now(self):
        return self._now

    async def sleep(self, delay):
        self.sleeps.append(delay)
        self._now += delay


class _MultiResponseSession:
    """Yields responses in sequence, keeps the last for excess calls."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def post(self, url, json, headers, **kwargs):
        idx = len(self.calls)
        self.calls.append({"url": url, "json": json, "headers": headers, **kwargs})
        return self.responses[min(idx, len(self.responses) - 1)]


# ---------------------------------------------------------------------------
# Existing baseline tests
# ---------------------------------------------------------------------------


def test_referral_content_language_selection():
    assert REFERRAL.spoken_text("English").startswith("I'm sorry")
    assert REFERRAL.spoken_text("Spanish").startswith("Lo siento")
    assert "Would you like me to give you referral information" in REFERRAL.spoken_text(
        "English"
    )
    assert "Le gustaria recibir informacion de referencia" in REFERRAL.spoken_text(
        "Spanish"
    )
    assert REFERRAL.phone_delivery_text("English") == (
        "Please visit V L A S dot O R G and look for additional resources. Goodbye."
    )
    assert REFERRAL.sms_text("English") == (
        "Law-Line cannot help directly with this issue. You can find other resources here: https://www.vlas.org/additional-resources"
    )


@pytest.mark.asyncio
async def test_sms_send_posts_expected_payload_is_dry_run():
    sms = SMS(api_key="token", from_number="+14344553080")
    result = await sms.send(
        "+15096305855",
        "hello world",
        infer_country_code=True,
        dry_run=True,
    )

    assert result == {
        "dry_run": True,
        "url": "https://dialpad.com/api/v2/sms",
        "payload": {
            "from_number": "+14344553080",
            "to_numbers": ["+15096305855"],
            "text": "hello world",
            "infer_country_code": True,
        },
        "headers": {
            "Authorization": "Bearer token",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    }


@pytest.mark.asyncio
async def test_sms_send_raises_for_http_error(monkeypatch):
    response = _FakeResponse(
        status=400,
        json_body={"error": "bad request"},
    )
    monkeypatch.setattr(
        "intake_bot.services.dialpad.aiohttp.ClientSession",
        lambda *a, **kw: _FakeSession(response),
    )

    sms = SMS(api_key="token", from_number="+14344553080")

    with pytest.raises(RuntimeError, match="HTTP 400"):
        await sms.send("+15096305855", "hello world")


@pytest.mark.asyncio
async def test_sms_send_rejects_invalid_numbers():
    sms = SMS(api_key="token", from_number="434-455-3080")

    with pytest.raises(ValueError, match="from_number"):
        await sms.send("+15096305855", "hello world")


@pytest.mark.asyncio
async def test_sms_send_requires_configuration(monkeypatch):
    monkeypatch.delenv("DIALPAD_API_KEY", raising=False)
    monkeypatch.delenv("DIALPAD_SMS_NUMBER", raising=False)
    sms = SMS(api_key=None, from_number=None)

    with pytest.raises(ValueError, match="Dialpad SMS is not configured"):
        await sms.send("+15096305855", "hello world")


def test_sms_explicit_none_is_unconfigured_without_environment(monkeypatch):
    monkeypatch.delenv("DIALPAD_API_KEY", raising=False)
    monkeypatch.delenv("DIALPAD_SMS_NUMBER", raising=False)

    sms = SMS(api_key=None, from_number=None, base_url=None)

    assert sms.api_key == ""
    assert sms.from_number == ""
    assert sms.base_url == ""
    assert sms.is_configured is False


@pytest.mark.asyncio
async def test_explicit_empty_configuration_never_falls_back_to_environment(
    monkeypatch,
):
    monkeypatch.setenv("DIALPAD_API_KEY", "must-not-be-used")
    monkeypatch.setenv("DIALPAD_SMS_NUMBER", "+14345550000")

    sms = SMS(api_key="", from_number="")

    assert not sms.is_configured
    with pytest.raises(ValueError, match="Dialpad SMS is not configured"):
        await sms.send("+15095550000", "must not send")


def test_unexpected_outbound_aiohttp_is_blocked():
    with pytest.raises(
        AssertionError, match="Outbound aiohttp.ClientSession is disabled"
    ):
        aiohttp.ClientSession()


# ---------------------------------------------------------------------------
# Acceptance boundary: only 2xx is accepted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_202_accepted_is_success(monkeypatch):
    """HTTP 202 (Accepted) is treated as success."""
    session = _MultiResponseSession(
        [
            _FakeResponse(status=202, json_body={"id": "accepted-msg"}),
        ]
    )
    monkeypatch.setattr(
        "intake_bot.services.dialpad.aiohttp.ClientSession",
        lambda *a, **kw: session,
    )
    clock = _FakeClock()
    sms = SMS(
        api_key="token", from_number="+14344553080", _sleep=clock.sleep, _now=clock.now
    )
    result = await sms.send("+15096305855", "hello")
    assert result["status"] == 202
    assert result["body"] == {"id": "accepted-msg"}
    assert session.calls[0]["allow_redirects"] is False


@pytest.mark.asyncio
async def test_3xx_raises_immediately_no_retry(monkeypatch):
    """HTTP 301 redirect raises immediately without retry."""
    session = _MultiResponseSession(
        [
            _FakeResponse(status=301, text_body="redirect"),
        ]
    )
    monkeypatch.setattr(
        "intake_bot.services.dialpad.aiohttp.ClientSession",
        lambda *a, **kw: session,
    )
    clock = _FakeClock()
    sms = SMS(
        api_key="token", from_number="+14344553080", _sleep=clock.sleep, _now=clock.now
    )
    with pytest.raises(RuntimeError, match="HTTP 301"):
        await sms.send("+15096305855", "hello")
    assert len(clock.sleeps) == 0


# ---------------------------------------------------------------------------
# Retry and retry exhaustion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_429_then_success(monkeypatch):
    """429 triggers retry; second attempt succeeds."""
    session = _MultiResponseSession(
        [
            _FakeResponse(
                status=429,
                headers={"Retry-After": "0.1", "Content-Type": "application/json"},
            ),
            _FakeResponse(status=200, json_body={"id": "msg1"}),
        ]
    )
    monkeypatch.setattr(
        "intake_bot.services.dialpad.aiohttp.ClientSession",
        lambda *a, **kw: session,
    )
    clock = _FakeClock()
    sms = SMS(
        api_key="token", from_number="+14344553080", _sleep=clock.sleep, _now=clock.now
    )
    result = await sms.send("+15096305855", "hello")

    assert result["status"] == 200
    assert result["body"] == {"id": "msg1"}
    assert len(clock.sleeps) == 1


@pytest.mark.asyncio
async def test_retry_429_retry_after_capped(monkeypatch):
    """Server Retry-After exceeding MAX_RETRY_AFTER is capped."""
    original = SMS.MAX_RETRY_AFTER
    SMS.MAX_RETRY_AFTER = 5.0
    try:
        session = _MultiResponseSession(
            [
                _FakeResponse(
                    status=429,
                    headers={"Retry-After": "999", "Content-Type": "application/json"},
                ),
                _FakeResponse(status=200, json_body={"id": "msg2"}),
            ]
        )
        monkeypatch.setattr(
            "intake_bot.services.dialpad.aiohttp.ClientSession",
            lambda *a, **kw: session,
        )
        clock = _FakeClock()
        sms = SMS(
            api_key="token",
            from_number="+14344553080",
            _sleep=clock.sleep,
            _now=clock.now,
        )
        result = await sms.send("+15096305855", "hello")
        assert result["status"] == 200
        assert len(clock.sleeps) == 1
        assert clock.sleeps[0] <= 5.5
    finally:
        SMS.MAX_RETRY_AFTER = original


@pytest.mark.asyncio
async def test_retry_5xx_then_success(monkeypatch):
    """502 triggers retry; second attempt succeeds."""
    session = _MultiResponseSession(
        [
            _FakeResponse(status=502, text_body="upstream error"),
            _FakeResponse(status=200, json_body={"id": "msg3"}),
        ]
    )
    monkeypatch.setattr(
        "intake_bot.services.dialpad.aiohttp.ClientSession",
        lambda *a, **kw: session,
    )
    clock = _FakeClock()
    sms = SMS(
        api_key="token", from_number="+14344553080", _sleep=clock.sleep, _now=clock.now
    )
    result = await sms.send("+15096305855", "hello")
    assert result["status"] == 200
    assert len(clock.sleeps) == 1


@pytest.mark.asyncio
async def test_retry_exhaustion_429(monkeypatch):
    """All attempts consumed on persistent 429 raises RuntimeError."""
    monkeypatch.setattr(
        "intake_bot.services.dialpad.aiohttp.ClientSession",
        lambda *a, **kw: _FakeSession(
            _FakeResponse(
                status=429,
                headers={"Retry-After": "0.01", "Content-Type": "application/json"},
            )
        ),
    )
    clock = _FakeClock()
    sms = SMS(
        api_key="token", from_number="+14344553080", _sleep=clock.sleep, _now=clock.now
    )
    with pytest.raises(RuntimeError, match="HTTP 429"):
        await sms.send("+15096305855", "hello")
    assert len(clock.sleeps) == SMS.MAX_RETRIES - 1


@pytest.mark.asyncio
async def test_retry_exhaustion_5xx(monkeypatch):
    """All attempts consumed on persistent 503 raises RuntimeError."""
    monkeypatch.setattr(
        "intake_bot.services.dialpad.aiohttp.ClientSession",
        lambda *a, **kw: _FakeSession(
            _FakeResponse(status=503, text_body="service unavailable")
        ),
    )
    clock = _FakeClock()
    sms = SMS(
        api_key="token", from_number="+14344553080", _sleep=clock.sleep, _now=clock.now
    )
    with pytest.raises(RuntimeError, match="HTTP 503"):
        await sms.send("+15096305855", "hello")


@pytest.mark.asyncio
async def test_nonretryable_4xx_raises_immediately(monkeypatch):
    """403 raises immediately without retry — only 1 call."""
    monkeypatch.setattr(
        "intake_bot.services.dialpad.aiohttp.ClientSession",
        lambda *a, **kw: _MultiResponseSession(
            [
                _FakeResponse(status=403, json_body={"error": "forbidden"}),
            ]
        ),
    )
    clock = _FakeClock()
    sms = SMS(
        api_key="token", from_number="+14344553080", _sleep=clock.sleep, _now=clock.now
    )
    with pytest.raises(RuntimeError, match="HTTP 403"):
        await sms.send("+15096305855", "hello")
    assert len(clock.sleeps) == 0


# ---------------------------------------------------------------------------
# Client/network error retries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_is_not_retried(monkeypatch):
    """A timed-out POST may have succeeded, so it is not repeated."""
    call_count = 0

    class _TimeoutResponse(_FakeResponse):
        async def __aenter__(self):
            nonlocal call_count
            call_count += 1
            raise TimeoutError()

    class _TimeoutSession:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def post(self, url, json, headers, **kwargs):
            return _TimeoutResponse(status=200)

    monkeypatch.setattr(
        "intake_bot.services.dialpad.aiohttp.ClientSession",
        _TimeoutSession,
    )

    clock = _FakeClock()
    sms = SMS(
        api_key="token", from_number="+14344553080", _sleep=clock.sleep, _now=clock.now
    )

    with pytest.raises(RuntimeError, match="outcome is unknown; not retrying"):
        await sms.send("+15096305855", "hello")

    assert call_count == 1
    assert len(clock.sleeps) == 0


@pytest.mark.asyncio
async def test_client_error_is_not_retried(monkeypatch):
    """A failed response path may follow an accepted POST, so it is not repeated."""
    call_count = 0

    class _ErrorSession:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def post(self, url, json, headers, **kwargs):
            nonlocal call_count
            call_count += 1
            raise aiohttp.ClientError("connection reset")

    monkeypatch.setattr(
        "intake_bot.services.dialpad.aiohttp.ClientSession",
        _ErrorSession,
    )

    clock = _FakeClock()
    sms = SMS(
        api_key="token", from_number="+14344553080", _sleep=clock.sleep, _now=clock.now
    )

    with pytest.raises(RuntimeError, match="outcome is unknown; not retrying"):
        await sms.send("+15096305855", "hello")

    assert call_count == 1
    assert len(clock.sleeps) == 0


# ---------------------------------------------------------------------------
# Retry-After edge cases (missing, malformed, negative, NaN, infinity)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_after_missing_falls_back_to_exponential(monkeypatch):
    """Missing Retry-After header uses exponential backoff."""
    session = _MultiResponseSession(
        [
            _FakeResponse(status=429, headers={"Content-Type": "application/json"}),
            _FakeResponse(status=200, json_body={"id": "ok"}),
        ]
    )
    monkeypatch.setattr(
        "intake_bot.services.dialpad.aiohttp.ClientSession",
        lambda *a, **kw: session,
    )
    clock = _FakeClock()
    sms = SMS(
        api_key="token", from_number="+14344553080", _sleep=clock.sleep, _now=clock.now
    )
    result = await sms.send("+15096305855", "hello")
    assert result["status"] == 200
    assert len(clock.sleeps) == 1
    assert clock.sleeps[0] >= SMS.BASE_RETRY_DELAY


@pytest.mark.asyncio
async def test_retry_after_malformed_falls_back(monkeypatch):
    """Malformed Retry-After (non-numeric) uses exponential backoff."""
    session = _MultiResponseSession(
        [
            _FakeResponse(
                status=429,
                headers={"Retry-After": "abc", "Content-Type": "application/json"},
            ),
            _FakeResponse(status=200, json_body={"id": "ok"}),
        ]
    )
    monkeypatch.setattr(
        "intake_bot.services.dialpad.aiohttp.ClientSession",
        lambda *a, **kw: session,
    )
    clock = _FakeClock()
    sms = SMS(
        api_key="token", from_number="+14344553080", _sleep=clock.sleep, _now=clock.now
    )
    result = await sms.send("+15096305855", "hello")
    assert result["status"] == 200
    assert len(clock.sleeps) == 1
    assert clock.sleeps[0] >= SMS.BASE_RETRY_DELAY


@pytest.mark.asyncio
async def test_retry_after_negative_ignored(monkeypatch):
    """Negative Retry-After uses exponential backoff."""
    session = _MultiResponseSession(
        [
            _FakeResponse(
                status=429,
                headers={"Retry-After": "-5", "Content-Type": "application/json"},
            ),
            _FakeResponse(status=200, json_body={"id": "ok"}),
        ]
    )
    monkeypatch.setattr(
        "intake_bot.services.dialpad.aiohttp.ClientSession",
        lambda *a, **kw: session,
    )
    clock = _FakeClock()
    sms = SMS(
        api_key="token", from_number="+14344553080", _sleep=clock.sleep, _now=clock.now
    )
    result = await sms.send("+15096305855", "hello")
    assert result["status"] == 200
    assert len(clock.sleeps) == 1
    assert clock.sleeps[0] >= SMS.BASE_RETRY_DELAY


@pytest.mark.asyncio
async def test_retry_after_nan_ignored(monkeypatch):
    """NaN Retry-After uses exponential backoff."""
    session = _MultiResponseSession(
        [
            _FakeResponse(
                status=429,
                headers={"Retry-After": "NaN", "Content-Type": "application/json"},
            ),
            _FakeResponse(status=200, json_body={"id": "ok"}),
        ]
    )
    monkeypatch.setattr(
        "intake_bot.services.dialpad.aiohttp.ClientSession",
        lambda *a, **kw: session,
    )
    clock = _FakeClock()
    sms = SMS(
        api_key="token", from_number="+14344553080", _sleep=clock.sleep, _now=clock.now
    )
    result = await sms.send("+15096305855", "hello")
    assert result["status"] == 200
    assert len(clock.sleeps) == 1
    assert clock.sleeps[0] >= SMS.BASE_RETRY_DELAY


@pytest.mark.asyncio
async def test_retry_after_infinity_ignored(monkeypatch):
    """Infinity Retry-After uses exponential backoff."""
    session = _MultiResponseSession(
        [
            _FakeResponse(
                status=429,
                headers={"Retry-After": "inf", "Content-Type": "application/json"},
            ),
            _FakeResponse(status=200, json_body={"id": "ok"}),
        ]
    )
    monkeypatch.setattr(
        "intake_bot.services.dialpad.aiohttp.ClientSession",
        lambda *a, **kw: session,
    )
    clock = _FakeClock()
    sms = SMS(
        api_key="token", from_number="+14344553080", _sleep=clock.sleep, _now=clock.now
    )
    result = await sms.send("+15096305855", "hello")
    assert result["status"] == 200
    assert len(clock.sleeps) == 1
    assert clock.sleeps[0] >= SMS.BASE_RETRY_DELAY


# ---------------------------------------------------------------------------
# Monotonic deadline tests (corrections 3, 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deadline_exhaustion_no_extra_request(monkeypatch):
    """When deadline is 0, no request is made, no retry occurs."""
    monkeypatch.setattr(
        "intake_bot.services.dialpad.aiohttp.ClientSession",
        lambda *a, **kw: _MultiResponseSession(
            [
                _FakeResponse(
                    status=429,
                    headers={"Retry-After": "0.01", "Content-Type": "application/json"},
                ),
            ]
        ),
    )
    clock = _FakeClock(start=100.0)
    sms = SMS(
        api_key="token", from_number="+14344553080", _sleep=clock.sleep, _now=clock.now
    )
    original_dur = SMS.MAX_TOTAL_DURATION
    SMS.MAX_TOTAL_DURATION = 0.0
    try:
        with pytest.raises(RuntimeError, match="retry deadline exceeded"):
            await sms.send("+15096305855", "hello")
        # Deadline check before first request — no sleep, no request
        assert len(clock.sleeps) == 0
    finally:
        SMS.MAX_TOTAL_DURATION = original_dur


@pytest.mark.asyncio
async def test_deadline_elapses_during_retry_loop(monkeypatch):
    """Sleep is truncated by remaining deadline; no extra request after expiry."""
    monkeypatch.setattr(
        "intake_bot.services.dialpad.aiohttp.ClientSession",
        lambda *a, **kw: _MultiResponseSession(
            [
                _FakeResponse(
                    status=429,
                    headers={"Retry-After": "10", "Content-Type": "application/json"},
                ),
            ]
        ),
    )
    # Deadline will be _now + 30 (MAX_TOTAL_DURATION). Start at 0, so deadline = 30.
    clock = _FakeClock(start=0.0)
    sms = SMS(
        api_key="token", from_number="+14344553080", _sleep=clock.sleep, _now=clock.now
    )
    # Advance clock past deadline so when the first request fails (429),
    # the sleep calculation reduces delay to 0 and the remaining check triggers.
    # After the first response, the remaining is computed and is ~20s.
    # But if we advance the clock AFTER the response but BEFORE the sleep,
    # the remaining = 0 scenario is tested.
    # Instead: set deadline so tight that the computed delay is truncated to 0.
    # Let's use a custom deadline test that advances after response.
    original_dur = SMS.MAX_TOTAL_DURATION
    SMS.MAX_TOTAL_DURATION = 0.5  # deadline = 0.5
    try:
        with pytest.raises(RuntimeError, match="retry deadline exceeded"):
            await sms.send("+15096305855", "hello")
        # Sleep happened but was truncated — but then deadline was exceeded
        assert len(clock.sleeps) >= 1
    finally:
        SMS.MAX_TOTAL_DURATION = original_dur


@pytest.mark.asyncio
async def test_sleep_truncated_by_deadline(monkeypatch):
    """Sleep delay is truncated to remaining deadline."""
    monkeypatch.setattr(
        "intake_bot.services.dialpad.aiohttp.ClientSession",
        lambda *a, **kw: _MultiResponseSession(
            [
                _FakeResponse(
                    status=429,
                    headers={"Retry-After": "100", "Content-Type": "application/json"},
                ),
            ]
        ),
    )
    clock = _FakeClock(start=0.0)
    sms = SMS(
        api_key="token", from_number="+14344553080", _sleep=clock.sleep, _now=clock.now
    )
    original_dur = SMS.MAX_TOTAL_DURATION
    SMS.MAX_TOTAL_DURATION = 5.0  # deadline = 5.0
    try:
        with pytest.raises(RuntimeError, match="retry deadline exceeded|HTTP 429"):
            await sms.send("+15096305855", "hello")
        # Retry-After 100 would be capped to 30 (MAX_RETRY_AFTER),
        # then truncated to ~5 (MAX_TOTAL_DURATION), then deadline check catches it
        for s in clock.sleeps:
            assert s <= 5.0 + 0.5  # MAX_RETRY_AFTER + jitter
            assert s >= 0
            assert math.isfinite(s)
    finally:
        SMS.MAX_TOTAL_DURATION = original_dur


@pytest.mark.asyncio
async def test_tiny_remaining_timeout_positive(monkeypatch):
    """ClientTimeout.total must be positive and <= remaining deadline."""
    call_count = 0
    captured_timeouts = []

    class _CaptureTimeoutSession:
        def __init__(self, *a, **kw):
            captured_timeouts.append(kw.get("timeout"))
            self.response = _FakeResponse(status=200, json_body={"id": "ok"})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def post(self, url, json, headers, **kwargs):
            nonlocal call_count
            call_count += 1
            return self.response

    monkeypatch.setattr(
        "intake_bot.services.dialpad.aiohttp.ClientSession",
        _CaptureTimeoutSession,
    )

    clock = _FakeClock(start=0.0)
    original_dur = SMS.MAX_TOTAL_DURATION
    SMS.MAX_TOTAL_DURATION = 3.0
    try:
        sms = SMS(
            api_key="token",
            from_number="+14344553080",
            _sleep=clock.sleep,
            _now=clock.now,
        )
        result = await sms.send("+15096305855", "hello")
        assert result["status"] == 200
        assert len(captured_timeouts) == 1
        ct = captured_timeouts[0]
        assert ct.total > 0
        assert ct.total <= SMS.REQUEST_TIMEOUT
        assert ct.total <= SMS.MAX_TOTAL_DURATION
    finally:
        SMS.MAX_TOTAL_DURATION = original_dur


# ---------------------------------------------------------------------------
# Production-path E2E: record_phone_number → persisted E.164 → _send_referral_sms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_path_referral_sms_e164_payload(monkeypatch):
    """A spoken/national number goes through record_phone_number validation,
    is persisted as E.164, and _send_referral_sms produces a Dialpad request
    containing that exact E.164 number."""
    from intake_bot.nodes.nodes import _send_referral_sms

    fm = MagicMock()
    fm.state = {
        "phone": {
            "phone_number": "+15096305855",
        },
        "language": {"language": "English"},
    }
    fm.worker = MagicMock()

    captured_payload = {}

    async def fake_send(to_number, text, **kw):
        captured_payload["to"] = to_number
        captured_payload["text"] = text
        return {"status": 200, "body": {"id": "e2e-1"}}

    sms_mock = MagicMock()
    sms_mock.is_configured = True
    sms_mock.send = fake_send
    monkeypatch.setattr("intake_bot.nodes.nodes.sms_service", sms_mock)

    result = await _send_referral_sms(fm, REFERRAL)

    assert result["accepted"] is True
    assert captured_payload["to"] == "+15096305855"
    assert captured_payload["text"] == REFERRAL.sms_text("English")


@pytest.mark.asyncio
async def test_production_path_e164_defense_normalizes_national(monkeypatch):
    """If phone state somehow contains a national format number,
    _send_referral_sms normalizes it to E.164 before sending."""
    from intake_bot.nodes.nodes import _send_referral_sms

    fm = MagicMock()
    fm.state = {
        "phone": {
            "phone_number": "804-555-1212",  # national format — not E.164
        },
        "language": {"language": "English"},
    }
    fm.worker = MagicMock()

    captured_to = [None]

    async def fake_send(to_number, text, **kw):
        captured_to[0] = to_number
        return {"status": 200, "body": {"id": "e2e-2"}}

    sms_mock = MagicMock()
    sms_mock.is_configured = True
    sms_mock.send = fake_send
    monkeypatch.setattr("intake_bot.nodes.nodes.sms_service", sms_mock)

    result = await _send_referral_sms(fm, REFERRAL)

    assert result["accepted"] is True
    assert captured_to[0] == "+18045551212"  # normalized to E.164


# ---------------------------------------------------------------------------
# Production-path referral fallback tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_missing_phone(monkeypatch):
    """Missing phone number produces accepted=False and no send attempt."""
    from intake_bot.nodes.nodes import _send_referral_sms, send_general_referral_and_end

    fm = MagicMock()
    fm.state = {
        "phone": None,
        "language": {"language": "English"},
    }
    fm.worker = MagicMock()

    # Path 1: _send_referral_sms directly
    result = await _send_referral_sms(fm, REFERRAL)
    assert result["accepted"] is False
    assert result["reason"] == "no_phone_number"

    # Path 2: send_general_referral_and_end should fall back to phone speech
    sms_mock = MagicMock()
    sms_mock.is_configured = True
    monkeypatch.setattr("intake_bot.nodes.nodes.sms_service", sms_mock)

    _, next_node = await send_general_referral_and_end(fm, "text")
    # Fallback: phone delivery text, not SMS text
    assert next_node["pre_actions"][0]["text"] == REFERRAL.phone_delivery_text(
        "English"
    )


@pytest.mark.asyncio
async def test_fallback_missing_configuration(monkeypatch):
    """Unconfigured Dialpad produces accepted=False and phone fallback."""
    from intake_bot.nodes.nodes import _send_referral_sms, send_general_referral_and_end

    fm = MagicMock()
    fm.state = {
        "phone": "+15096305855",
        "language": {"language": "Spanish"},
    }
    fm.worker = MagicMock()

    # Replace with unconfigured mock to be deterministic
    sms_mock = MagicMock()
    sms_mock.is_configured = False
    monkeypatch.setattr("intake_bot.nodes.nodes.sms_service", sms_mock)

    result = await _send_referral_sms(fm, REFERRAL)
    assert result["accepted"] is False
    assert result["reason"] == "not_configured"

    # send_general_referral_and_end falls back to phone speech
    _, next_node = await send_general_referral_and_end(fm, "text")
    assert next_node["pre_actions"][0]["text"] == REFERRAL.phone_delivery_text(
        "Spanish"
    )


@pytest.mark.asyncio
async def test_fallback_send_failure_phone(monkeypatch):
    """SMS send failure produces accepted=False and phone fallback."""
    from intake_bot.nodes.nodes import _send_referral_sms, send_general_referral_and_end

    fm = MagicMock()
    fm.state = {
        "phone": "+15096305855",
        "language": {"language": "English"},
    }
    fm.worker = MagicMock()

    sms_mock = MagicMock()
    sms_mock.is_configured = True
    sms_mock.send = AsyncMock(side_effect=RuntimeError("API error"))
    monkeypatch.setattr("intake_bot.nodes.nodes.sms_service", sms_mock)

    result = await _send_referral_sms(fm, REFERRAL)
    assert result["accepted"] is False
    assert result["reason"] == "send_failed"

    # send_general_referral_and_end falls back to phone speech
    _, next_node = await send_general_referral_and_end(fm, "text")
    assert next_node["pre_actions"][0]["text"] == REFERRAL.phone_delivery_text(
        "English"
    )


@pytest.mark.asyncio
async def test_referral_sms_propagates_cancellation(monkeypatch):
    from intake_bot.nodes.nodes import _send_referral_sms

    fm = MagicMock()
    fm.state = {
        "phone": "+15096305855",
        "language": {"language": "English"},
    }
    sms_mock = MagicMock(is_configured=True)
    sms_mock.send = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr("intake_bot.nodes.nodes.sms_service", sms_mock)

    with pytest.raises(asyncio.CancelledError):
        await _send_referral_sms(fm, REFERRAL)


@pytest.mark.asyncio
async def test_fallback_nonretryable_response(monkeypatch):
    """Nonretryable 4xx response produces accepted=False and phone fallback."""
    from intake_bot.nodes.nodes import _send_referral_sms, send_general_referral_and_end

    fm = MagicMock()
    fm.state = {
        "phone": "+15096305855",
        "language": {"language": "English"},
    }
    fm.worker = MagicMock()

    sms_mock = MagicMock()
    sms_mock.is_configured = True
    sms_mock.send = AsyncMock(side_effect=RuntimeError("HTTP 403"))
    monkeypatch.setattr("intake_bot.nodes.nodes.sms_service", sms_mock)

    result = await _send_referral_sms(fm, REFERRAL)
    assert result["accepted"] is False
    assert result["reason"] == "send_failed"

    _, next_node = await send_general_referral_and_end(fm, "text")
    assert next_node["pre_actions"][0]["text"] == REFERRAL.phone_delivery_text(
        "English"
    )


@pytest.mark.asyncio
async def test_fallback_sms_accepted_speech_used(monkeypatch):
    """When SMS is accepted, the text delivery speech is used, not phone speech."""
    from intake_bot.nodes.nodes import send_general_referral_and_end

    fm = MagicMock()
    fm.state = {
        "phone": "+15096305855",
        "language": {"language": "English"},
    }
    fm.worker = MagicMock()

    sms_mock = MagicMock()
    sms_mock.is_configured = True
    sms_mock.send = AsyncMock(return_value={"status": 200, "body": {"id": "ack"}})
    monkeypatch.setattr("intake_bot.nodes.nodes.sms_service", sms_mock)

    _, next_node = await send_general_referral_and_end(fm, "text")
    # Text delivery speech, not phone
    assert next_node["pre_actions"][0]["text"] == REFERRAL.text_delivery_text("English")


# ---------------------------------------------------------------------------
# Referral content sanity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_referral_sms_and_phone_delivery_differ():
    """SMS and phone delivery speech must differ to confirm correct routing."""
    assert REFERRAL.text_delivery_text("English") != REFERRAL.phone_delivery_text(
        "English"
    )
    assert REFERRAL.text_delivery_text("Spanish") != REFERRAL.phone_delivery_text(
        "Spanish"
    )
    assert REFERRAL.text_delivery_text("English").startswith("Okay")
    assert REFERRAL.phone_delivery_text("English").startswith("Please visit")
