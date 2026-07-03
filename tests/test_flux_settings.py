import os

import pytest

from intake_bot.bot import _get_flux_settings


@pytest.fixture(autouse=True)
def _clean_env():
    saved = {}
    keys = [
        "DEEPGRAM_FLUX_EAGER_EOT_THRESHOLD",
        "DEEPGRAM_FLUX_EOT_THRESHOLD",
        "DEEPGRAM_FLUX_EOT_TIMEOUT_MS",
        "DEEPGRAM_FLUX_MIN_CONFIDENCE",
    ]
    for key in keys:
        saved[key] = os.environ.pop(key, None)
    yield
    for key in keys:
        if saved[key] is not None:
            os.environ[key] = saved[key]
        else:
            os.environ.pop(key, None)


def test_flux_settings_defaults():
    settings = _get_flux_settings("test-call")
    assert settings["eager_eot_threshold"] == 0.6
    assert settings["eot_threshold"] == 0.6
    assert settings["eot_timeout_ms"] == 800
    assert settings["min_confidence"] == 0.5


def test_flux_settings_ws_test_defaults():
    settings = _get_flux_settings("ws-test-123")
    assert settings["eager_eot_threshold"] == 0.3
    assert settings["eot_threshold"] == 0.3
    assert settings["eot_timeout_ms"] == 1500
    assert settings["min_confidence"] == 0.1


def test_flux_settings_custom_values(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_FLUX_EAGER_EOT_THRESHOLD", "0.8")
    monkeypatch.setenv("DEEPGRAM_FLUX_EOT_THRESHOLD", "0.9")
    monkeypatch.setenv("DEEPGRAM_FLUX_EOT_TIMEOUT_MS", "500")
    monkeypatch.setenv("DEEPGRAM_FLUX_MIN_CONFIDENCE", "0.7")
    settings = _get_flux_settings("test-call")
    assert settings["eager_eot_threshold"] == 0.8
    assert settings["eot_threshold"] == 0.9
    assert settings["eot_timeout_ms"] == 500
    assert settings["min_confidence"] == 0.7
