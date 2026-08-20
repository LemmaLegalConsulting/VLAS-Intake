import os

import pytest

from intake_bot.utils.ev import ev_is_true, get_deepgram_tts_voices, get_ev, require_ev


@pytest.fixture(autouse=True)
def clear_deepgram_voice_environment(monkeypatch):
    for key in list(os.environ):
        if key.startswith("DEEPGRAM_TTS_VOICE_"):
            monkeypatch.delenv(key, raising=False)


def test_get_ev_returns_existing_value(monkeypatch):
    monkeypatch.setenv("SOME_KEY", "configured")

    assert get_ev("SOME_KEY", default="fallback") == "configured"


def test_get_ev_returns_default_when_missing(monkeypatch):
    monkeypatch.delenv("MISSING_KEY", raising=False)

    assert get_ev("MISSING_KEY", default="fallback") == "fallback"


def test_get_ev_returns_default_when_blank_and_default_provided(monkeypatch):
    monkeypatch.setenv("BLANK_KEY", "   ")

    assert get_ev("BLANK_KEY", default="fallback") == "fallback"


def test_get_ev_returns_empty_string_when_blank_with_implicit_default(monkeypatch):
    monkeypatch.setenv("BLANK_KEY", "   ")

    assert get_ev("BLANK_KEY") == ""


def test_require_ev_returns_existing_value(monkeypatch):
    monkeypatch.setenv("REQUIRED_KEY", "configured")

    assert require_ev("REQUIRED_KEY") == "configured"


def test_require_ev_raises_when_missing(monkeypatch):
    monkeypatch.delenv("REQUIRED_KEY", raising=False)

    with pytest.raises(ValueError, match="REQUIRED_KEY"):
        require_ev("REQUIRED_KEY")


def test_require_ev_raises_when_blank(monkeypatch):
    monkeypatch.setenv("REQUIRED_KEY", "")

    with pytest.raises(ValueError, match="REQUIRED_KEY"):
        require_ev("REQUIRED_KEY")


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("true", True),
        ("TRUE", True),
        ("  true  ", True),
        ("1", True),
        ("false", False),
        ("0", False),
        ("", False),
    ],
)
def test_ev_is_true_parses_boolean_values(monkeypatch, raw_value, expected):
    monkeypatch.setenv("BOOL_KEY", raw_value)

    assert ev_is_true("BOOL_KEY") is expected


def test_ev_is_true_returns_false_when_missing(monkeypatch):
    monkeypatch.delenv("BOOL_KEY", raising=False)

    assert ev_is_true("BOOL_KEY") is False


def test_get_deepgram_tts_voices_uses_default_voice_without_language(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_TTS_VOICE_EN", raising=False)
    monkeypatch.delenv("DEEPGRAM_TTS_VOICE_ES", raising=False)

    voice = get_deepgram_tts_voices()

    assert voice == "flux-alexis-en"


def test_get_deepgram_tts_voices_uses_language_default_without_env_override(
    monkeypatch,
):
    monkeypatch.delenv("DEEPGRAM_TTS_VOICE_EN", raising=False)
    monkeypatch.delenv("DEEPGRAM_TTS_VOICE_ES", raising=False)

    voice = get_deepgram_tts_voices("ES")

    assert voice == "aura-2-olivia-es"


def test_get_deepgram_tts_voices_prefers_exact_language_code_match(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_TTS_VOICE_EN", "flux-drew-en")
    monkeypatch.setenv("DEEPGRAM_TTS_VOICE_ES", "aura-2-celeste-es")

    english_voice = get_deepgram_tts_voices("EN")
    spanish_voice = get_deepgram_tts_voices("ES")

    assert english_voice == "flux-drew-en"
    assert spanish_voice == "aura-2-celeste-es"


def test_get_deepgram_tts_voices_matches_locale_suffix(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_TTS_VOICE_EN", raising=False)
    monkeypatch.setenv("DEEPGRAM_TTS_VOICE_ES_US", "aura-2-celeste-es")

    voice = get_deepgram_tts_voices("ES")

    assert voice == "aura-2-celeste-es"


def test_get_deepgram_tts_voices_returns_global_default_for_unknown_language(
    monkeypatch,
):
    monkeypatch.delenv("DEEPGRAM_TTS_VOICE_EN", raising=False)
    monkeypatch.delenv("DEEPGRAM_TTS_VOICE_FR", raising=False)

    voice = get_deepgram_tts_voices("FR")

    assert voice == "flux-alexis-en"


def test_get_deepgram_tts_voices_does_not_use_bare_env_fallback(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_TTS_VOICE", "flux-ignored-en")
    monkeypatch.delenv("DEEPGRAM_TTS_VOICE_EN", raising=False)

    voice = get_deepgram_tts_voices("EN")

    assert voice == "flux-alexis-en"
