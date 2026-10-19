from unittest.mock import patch

from intake_bot.services.poverty import (
    poverty_scale_get_income_limit,
    poverty_scale_income_qualifies,
)


def test_income_limit_returns_valid_limit():
    with patch(
        "intake_bot.services.poverty.get_poverty_scale_data",
        return_value={"poverty_base": 100, "poverty_increment": 25},
    ):
        assert poverty_scale_get_income_limit(household_size=2) == 125


def test_zero_income_limit_is_not_missing():
    with patch(
        "intake_bot.services.poverty.poverty_scale_get_income_limit",
        return_value=0,
    ):
        assert poverty_scale_income_qualifies(0) is True
        assert poverty_scale_income_qualifies(1) is False


def test_fractional_income_above_zero_limit_does_not_qualify():
    with patch(
        "intake_bot.services.poverty.poverty_scale_get_income_limit",
        return_value=0,
    ):
        assert poverty_scale_income_qualifies(0.5) is False


def test_missing_income_limit_returns_none():
    with patch(
        "intake_bot.services.poverty.poverty_scale_get_income_limit",
        return_value=None,
    ):
        assert poverty_scale_income_qualifies(0) is None


def test_income_at_monthly_limit_qualifies():
    with patch(
        "intake_bot.services.poverty.poverty_scale_get_income_limit",
        return_value=1200,
    ):
        assert poverty_scale_income_qualifies(100) is True
        assert poverty_scale_income_qualifies(101) is False
