import pytest
from intake_bot.services.dialpad import CASE_TYPE_REFERRAL, GENERAL_REFERRAL, SMS


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

    def post(self, url, json, headers):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self.response


@pytest.fixture(autouse=True)
def block_live_dialpad_http(monkeypatch):
    def _unexpected_client_session(*args, **kwargs):
        raise AssertionError(
            "Unexpected live aiohttp.ClientSession in test_dialpad.py. "
            "Tests must use dry_run=True or explicitly monkeypatch ClientSession."
        )

    monkeypatch.setattr(
        "intake_bot.services.dialpad.aiohttp.ClientSession",
        _unexpected_client_session,
    )


def test_referral_content_language_selection():
    assert GENERAL_REFERRAL.spoken_text("English").startswith("I'm sorry")
    assert GENERAL_REFERRAL.spoken_text("Spanish").startswith("Lo siento")
    assert CASE_TYPE_REFERRAL.sms_text("English") == (
        "Virginia State Bar referral information: https://www.vsb.org"
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
        lambda: _FakeSession(response),
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
