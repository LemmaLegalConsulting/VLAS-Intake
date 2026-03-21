import pytest
from intake_bot.utils.ev import ev_is_true, get_ev, require_ev


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
