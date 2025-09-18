import pytest
from intake_bot.intake_arg_models import (
    AssetEntry,
    Assets,
    HouseholdIncome,
    IncomeDetail,
    IncomePeriod,
    MemberIncome,
)
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
    "income,period,expected_eligible,expected_monthly_income",
    [
        (0, "month", True, 0),  # eligible, no income
        (0, "year", True, 0),  # eligible, no income
        (1200, "month", True, 1200),  # eligible, monthly income below 300% of poverty
        (40000, "year", True, 3333),  # eligible, yearly income below 300% of poverty
        (5000, "month", False, 5000),  # not eligible, monthly income above 300% of poverty
        (60000, "year", False, 5000),  # not eligible, yearly income above 300% of poverty
        (3911, "month", True, 3911),  # eligible, just below 300% poverty for month
        (46932, "year", True, 3911),  # eligible, just below 300% poverty for year
        (
            3912,
            "month",
            True,
            3912,
        ),  # eligible, near 300% poverty for month (truncated to 299)
        (46944, "year", True, 3912),  # eligible, near 300% poverty for year (truncated to 299)
        (
            3913,
            "month",
            False,
            3913,
        ),  # not eligible, just over 300% poverty for month due to round()
        (
            46956,
            "year",
            False,
            3913,
        ),  # not eligible, just over 300% poverty for month due to round()
        (100000, "year", False, 8333),  # not eligible, high yearly income
        (10000, "month", False, 10000),  # not eligible, high monthly income
    ],
)
async def test_check_income(income, period, expected_eligible, expected_monthly_income):
    remote = MockRemoteSystem()
    # Build HouseholdIncome input according to new model
    # We'll use a single household member "Test Person" with a single income type "wages"
    income_detail = IncomeDetail(amount=income, period=IncomePeriod(period))
    member_income = MemberIncome({"wages": income_detail})
    household_income = HouseholdIncome({"Test Person": member_income})
    is_eligible, monthly_income = await remote.check_income(income=household_income)
    assert is_eligible == expected_eligible
    assert monthly_income == expected_monthly_income


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "assets_data,expected_eligible,expected_value",
    [
        ([], True, 0),  # no assets
        ([{"car": 5000}], True, 5000),  # single asset below limit
        ([{"car": 5000}, {"savings": 2000}], True, 7000),  # multiple assets below limit
        ([{"house": 10000}], True, 10000),  # exactly at limit
        ([{"house": 10001}], False, 10001),  # just over limit
        ([{"car": 8000}, {"savings": 3000}], False, 11000),  # multiple assets over limit
        ([{"car": 0}], True, 0),  # zero value asset
        (
            [{"car": 5000}, {"boat": 0}, {"savings": 4999}],
            True,
            9999,
        ),  # just under limit with zero asset
        ([{"expensive_car": 15000}], False, 15000),  # single high-value asset
        (
            [{"car": 2500}, {"savings": 2500}, {"investments": 2500}, {"jewelry": 2500}],
            True,
            10000,
        ),  # multiple assets exactly at limit
    ],
)
async def test_check_assets(assets_data, expected_eligible, expected_value):
    remote = MockRemoteSystem()
    # Build Assets model from the test data
    asset_entries = [AssetEntry(asset) for asset in assets_data]
    assets = Assets(asset_entries)

    is_eligible, assets_value = await remote.check_assets(assets=assets)
    assert is_eligible == expected_eligible
    assert assets_value == expected_value
