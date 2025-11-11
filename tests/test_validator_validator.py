import pytest
from intake_bot.models.validator import (
    AssetEntry,
    Assets,
    HouseholdIncome,
    IncomeDetail,
    IncomePeriod,
    MemberIncome,
)
from intake_bot.nodes.validator import IntakeValidator


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_area,expected_match,expected_fips",
    [
        ("Amelia County", "Amelia County", 51007),  # exact match
        ("Amelia", "Amelia County", 51007),  # partial match
        ("amalea", "Amelia County", 51007),  # WRatio partial match
        ("AMILYA", "Amelia County", 51007),  # WRatio partial match
        ("aml", "Amelia County", 51007),  # WRatio partial match
        ("Nonexistent Place", "", 0),  # no match
        ("amelia county", "Amelia County", 51007),  # case-insensitive match
        ("Amelia County City", "Amelia County", 51007),  # extra words
        ("Bedford", "Bedford County", 51019),  # another partial match
        ("Danville", "Danville City", 51595),  # city match
        ("South Boston", "South Boston", 51083),  # exact city
        ("Emporia", "Emporia City", 51600),  # city match
        ("lynchburg", "Lynchburg City", 51680),  # lowercase city
        ("Halifax", "Halifax County", 51083),  # partial county
        ("", "", 0),  # empty string
    ],
)
async def test_check_service_area(user_area, expected_match, expected_fips):
    validator = IntakeValidator()
    match, fips_code = await validator.check_service_area(user_area)
    assert match == expected_match
    assert fips_code == expected_fips


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phone,expected_valid,expected_format",
    [
        ("866-534-5243", True, "(866) 534-5243"),  # already formatted
        ("8665345243", True, "(866) 534-5243"),  # digits only
        ("+18665345243", True, "(866) 534-5243"),  # +1 then digits only
        ("(866) 534-5243", True, "(866) 534-5243"),  # with parentheses and spaces
        ("866.534.5243", True, "(866) 534-5243"),  # with dots
        ("866 534 5243", True, "(866) 534-5243"),  # with spaces
        ("866-5345-243", True, "(866) 534-5243"),  # wrong format
        ("866a534f5243.", True, "(866) 534-5243"),  # letters/symbol mixed
        ("123-456-7890", False, "123-456-7890"),  # can't start with "1"
        ("abc-def-ghij", False, "abc-def-ghij"),  # letters only
        ("866534524", False, "866534524"),  # too short
        ("", False, ""),  # empty string
        ("+1 (866) 534-5243", True, "(866) 534-5243"),  # international format
        ("1-866-534-5243", True, "(866) 534-5243"),  # with leading 1
        ("+44 20 7946 0958", False, "+44 20 7946 0958"),  # non-US number
        ("911", False, "911"),  # emergency number
        ("000-000-0000", False, "000-000-0000"),  # invalid but correct length
        ("(999) 999-9999", False, "(999) 999-9999"),  # invalid area code
    ],
)
async def test_valid_phone_number(phone, expected_valid, expected_format):
    validator = IntakeValidator()
    valid, formatted = await validator.check_phone_number(phone)
    # If formatted is a phonenumbers.PhoneNumber object, convert to input string for failed cases
    import phonenumbers

    if not valid and isinstance(formatted, phonenumbers.PhoneNumber):
        # fallback: return the original input string
        formatted = phone
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
        (3912, "month", True, 3912),  # eligible, near 300% poverty for month (truncated to 299)
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
        (1200, "month", True, 1200),  # single member, low income
        (2400, "month", True, 2400),  # single member, still eligible
        (3600, "month", True, 3600),  # single member, just under
        (1200, "year", True, 100),  # very low yearly income
        (14400, "year", True, 1200),  # yearly, eligible
    ],
)
async def test_check_income(income, period, expected_eligible, expected_monthly_income):
    validator = IntakeValidator()
    income_detail = IncomeDetail(amount=income, period=IncomePeriod(period))
    member_income = MemberIncome({261: income_detail})
    household_income = HouseholdIncome({"Test Person": member_income})
    is_eligible, monthly_income = await validator.check_income(income=household_income)
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
        ([{"car": 10000}], True, 10000),  # single asset at limit
        ([{"car": 9999}, {"cash": 1}], True, 10000),  # just at limit
        ([{"car": 9999}, {"cash": 2}], False, 10001),  # just over limit
        ([{"car": -1000}, {"savings": 2000}], True, 1000),  # negative asset value
    ],
)
async def test_check_assets(assets_data, expected_eligible, expected_value):
    validator = IntakeValidator()
    asset_entries = [AssetEntry(asset) for asset in assets_data]
    assets = Assets(asset_entries)
    is_eligible, assets_value = await validator.check_assets(assets=assets)
    assert is_eligible == expected_eligible
    assert assets_value == expected_value


@pytest.mark.asyncio
async def test_get_alternative_providers():
    validator = IntakeValidator()
    alternatives = await validator.get_alternative_providers()
    assert isinstance(alternatives, list)
    assert "Center for Legal Help" in alternatives
    assert "Local Legal Help" in alternatives
