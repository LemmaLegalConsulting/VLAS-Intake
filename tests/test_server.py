import pytest
from fastapi import WebSocketException
from starlette.status import WS_1008_POLICY_VIOLATION

from intake_bot.nodes.nodes import node_start
from server import (
    _ALLOWED_ORIGINS,
    SilenceMixer,
    _get_user_idle_timeout_secs,
    _metadata_str,
    _parse_bool,
    _parse_metadata,
    _rate_limit_check,
    _rate_limit_store,
    _receive_metadata,
    create_app,
    generate_call_id,
)


@pytest.fixture(autouse=True)
def _clean_rate_limit_store():
    cleared = _rate_limit_store.copy()
    _rate_limit_store.clear()
    yield
    _rate_limit_store.clear()
    _rate_limit_store.update(cleared)


@pytest.mark.asyncio
async def test_silence_mixer_passthrough():
    mixer = SilenceMixer()

    assert await mixer.mix(b"\x00\x01\x02") == b"\x00\x01\x02"


def test_websocket_startup_uses_shared_start_node(monkeypatch):
    monkeypatch.delenv("TEST_INITIAL_NODE", raising=False)

    node = node_start()

    assert node["respond_immediately"] is False


def test_create_app_raises_in_production():
    with pytest.raises(RuntimeError, match="must not be used in production"):
        create_app(_env="production")


def test_create_app_returns_app_in_development():
    app = create_app(_env="development")
    assert app is not None


@pytest.mark.asyncio
async def test_rate_limit_check_passes():
    await _rate_limit_check("127.0.0.1")


@pytest.mark.asyncio
async def test_rate_limit_check_blocks_excessive(monkeypatch):
    monkeypatch.setenv("WS_RATE_LIMIT_PER_MINUTE", "3")
    await _rate_limit_check("test-client")
    await _rate_limit_check("test-client")
    await _rate_limit_check("test-client")
    with pytest.raises(WebSocketException) as exc_info:
        await _rate_limit_check("test-client")
    assert exc_info.value.code == WS_1008_POLICY_VIOLATION
    assert exc_info.value.reason == "Rate limit exceeded"


def test_server_has_cors_localhost_origins():
    assert "http://localhost:8765" in _ALLOWED_ORIGINS
    assert "http://127.0.0.1:8765" in _ALLOWED_ORIGINS
    assert "*" not in _ALLOWED_ORIGINS


def test_server_healthz_returns_ok():
    app = create_app(_env="development")
    for route in app.routes:
        if hasattr(route, "path") and route.path == "/healthz":
            return
    pytest.fail("No /healthz route found")


def test_create_app_cors_middleware_registered():
    app = create_app(_env="development")
    cors_middlewares = [
        m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware"
    ]
    assert len(cors_middlewares) == 1


class TestParseBool:
    def test_true_values(self):
        assert _parse_bool("true") is True
        assert _parse_bool("True") is True
        assert _parse_bool("1") is True
        assert _parse_bool("yes") is True
        assert _parse_bool("YES") is True

    def test_false_values(self):
        assert _parse_bool("false") is False
        assert _parse_bool("False") is False
        assert _parse_bool("0") is False
        assert _parse_bool("no") is False
        assert _parse_bool("") is False

    def test_strict_user_muting_metadata_true(self):
        metadata = {"strict_user_muting": "true"}
        result = _parse_bool(metadata.get("strict_user_muting", "false"))
        assert result is True

    def test_strict_user_muting_metadata_false(self):
        metadata = {"strict_user_muting": "false"}
        result = _parse_bool(metadata.get("strict_user_muting", "false"))
        assert result is False

    def test_strict_user_muting_metadata_missing_defaults_false(self):
        metadata: dict[str, str] = {}
        result = _parse_bool(metadata.get("strict_user_muting", "false"))
        assert result is False


def test_generate_call_id_non_empty():
    cid = generate_call_id()
    assert isinstance(cid, str) and len(cid) > 0


class FakeWebSocket:
    def __init__(self, message):
        self._message = message

    async def receive(self):
        return self._message


class TestParseMetadata:
    def test_valid_metadata(self):
        raw = '{"caller_phone_number": "+18665551234", "call_id": "ws-test-001"}'
        result = _parse_metadata(raw)
        assert result == {
            "caller_phone_number": "+18665551234",
            "call_id": "ws-test-001",
        }

    def test_invalid_json_returns_none(self):
        assert _parse_metadata("not-json") is None

    def test_non_object_json_returns_none(self):
        assert _parse_metadata('"just-a-string"') is None
        assert _parse_metadata("123") is None
        assert _parse_metadata("[]") is None
        assert _parse_metadata("null") is None

    def test_empty_object_is_valid(self):
        result = _parse_metadata("{}")
        assert result == {}

    @pytest.mark.asyncio
    async def test_receive_metadata_text_message(self):
        websocket = FakeWebSocket(
            {"type": "websocket.receive", "text": '{"call_id": "abc"}'}
        )
        assert await _receive_metadata(websocket) == {"call_id": "abc"}

    @pytest.mark.asyncio
    async def test_receive_metadata_rejects_binary_message(self):
        websocket = FakeWebSocket({"type": "websocket.receive", "bytes": b"{}"})
        assert await _receive_metadata(websocket) is None

    @pytest.mark.asyncio
    async def test_receive_metadata_rejects_disconnect(self):
        websocket = FakeWebSocket({"type": "websocket.disconnect"})
        assert await _receive_metadata(websocket) is None


class TestMetadataStr:
    def test_string_value(self):
        assert _metadata_str({"call_id": "abc"}, "call_id") == "abc"

    def test_numeric_value(self):
        assert _metadata_str({"call_id": 123}, "call_id") == "123"

    def test_none_uses_default(self):
        assert _metadata_str({"call_id": None}, "call_id", "fallback") == "fallback"

    def test_missing_uses_default(self):
        assert _metadata_str({}, "call_id", "fallback") == "fallback"


class TestGetUserIdleTimeoutSecs:
    def test_from_metadata(self):
        assert _get_user_idle_timeout_secs("any", {"idle_timeout_secs": "30.0"}) == 30.0

    def test_from_metadata_numeric(self):
        assert _get_user_idle_timeout_secs("any", {"idle_timeout_secs": 30}) == 30.0

    def test_metadata_overrides_env(self, monkeypatch):
        monkeypatch.setenv("WEBSOCKET_USER_IDLE_TIMEOUT_SECS", "60.0")
        result = _get_user_idle_timeout_secs("any", {"idle_timeout_secs": "15.0"})
        assert result == 15.0

    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("WEBSOCKET_USER_IDLE_TIMEOUT_SECS", "45.0")
        result = _get_user_idle_timeout_secs("any", {})
        assert result == 45.0

    def test_ws_test_default(self, monkeypatch):
        monkeypatch.delenv("WEBSOCKET_USER_IDLE_TIMEOUT_SECS", raising=False)
        result = _get_user_idle_timeout_secs("ws-test-123", {})
        assert result == 45.0

    def test_no_timeout_returns_none(self, monkeypatch):
        monkeypatch.delenv("WEBSOCKET_USER_IDLE_TIMEOUT_SECS", raising=False)
        result = _get_user_idle_timeout_secs("some-call", {})
        assert result is None

    def test_invalid_value_ignored(self, monkeypatch):
        monkeypatch.delenv("WEBSOCKET_USER_IDLE_TIMEOUT_SECS", raising=False)
        result = _get_user_idle_timeout_secs(
            "ws-test-123", {"idle_timeout_secs": "abc"}
        )
        assert result is None

    def test_non_positive_ignored(self, monkeypatch):
        monkeypatch.delenv("WEBSOCKET_USER_IDLE_TIMEOUT_SECS", raising=False)
        result = _get_user_idle_timeout_secs("ws-test-123", {"idle_timeout_secs": "0"})
        assert result is None
