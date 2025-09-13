import pytest
from intake_bot.remote import MockRemoteSystem  # type: ignore


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_area,expected_match",
    [
        ("Amelia County", "Amelia County"),  # exact match
        ("Amelia", "Amelia County"),  # partial match
        ("amalea", "Amelia County"),  # WRatio partial match
        ("AMILYA", "Amelia County"),  # WRatio partial match
        ("aml", "Amelia County"),  # WRatio partial match
        ("Nonexistent Place", ""),  # no match
        ("amelia county", "Amelia County"),  # case-insensitive match
        ("Amelia County City", "Amelia County"),  # extra words
    ],
)
async def test_check_service_area(user_area, expected_match):
    remote = MockRemoteSystem()
    match = await remote.check_service_area(user_area)
    for e in expected_match:
        assert e in match
    assert len(match) == len(expected_match)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phone,expected_valid,expected_format",
    [
        ("866-534-5243", True, "(866) 534-5243"),  # already formatted
        ("8665345243", True, "(866) 534-5243"),  # digits only
        ("(866) 534-5243", True, "(866) 534-5243"),  # with parentheses and spaces
        ("866.534.5243", True, "(866) 534-5243"),  # with dots
        ("866 534 5243", True, "(866) 534-5243"),  # with spaces
        ("866-5345-243", True, "(866) 534-5243"),  # wrong format
        ("866a534f5243.", True, "(866) 534-5243"),  # letters/symbol mixed
        ("123-456-7890", False, "123-456-7890"),  # can't start with "1"
        ("abc-def-ghij", False, "abc-def-ghij"),  # letters only
        ("866534524", False, "866534524"),  # too short
        ("", False, ""),  # empty string
    ],
)
async def test_valid_phone_number(phone, expected_valid, expected_format):
    remote = MockRemoteSystem()
    valid, formatted = await remote.valid_phone_number(phone)
    assert valid == expected_valid
    assert formatted == expected_format


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "income,period,expected_eligible,expected_monthly_income,expected_poverty_percent",
    [
        (0, "month", True, 0, 0),  # eligible, no income
        (0, "year", True, 0, 0),  # eligible, no income
        (1200, "month", True, 1200, 92),  # eligible, monthly income below 300% of poverty
        (40000, "year", True, 3333, 255),  # eligible, yearly income below 300% of poverty
        (5000, "month", False, 5000, 383),  # not eligible, monthly income above 300% of poverty
        (60000, "year", False, 5000, 383),  # not eligible, yearly income above 300% of poverty
        (3911, "month", True, 3911, 299),  # eligible, just below 300% poverty for month
        (46932, "year", True, 3911, 299),  # eligible, just below 300% poverty for year
        (3912, "month", True, 3912, 299),  # eligible, near 300% poverty for month (truncated to 299)
        (46944, "year", True, 3912, 299),  # eligible, near 300% poverty for year (truncated to 299)
        (3913, "month", True, 3913, 300),  # eligible, exactly 300% poverty for month
        (46956, "year", True, 3913, 300),  # eligible, exactly 300% poverty for year
        (100000, "year", False, 8333, 638),  # not eligible, high yearly income
        (10000, "month", False, 10000, 766),  # not eligible, high monthly income
    ],
)
async def test_check_income(income, period, expected_eligible, expected_monthly_income, expected_poverty_percent):
    remote = MockRemoteSystem()
    is_eligible, monthly_income, poverty_percent = await remote.check_income(income, period)
    assert is_eligible == expected_eligible
    assert monthly_income == expected_monthly_income
    assert poverty_percent == expected_poverty_percent
