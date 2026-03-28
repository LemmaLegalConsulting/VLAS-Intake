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
        (
            "The legal incident happened in Amelia County.",
            "Amelia County",
            51007,
        ),  # embedded canonical match
        ("amalea", "Amelia County", 51007),  # WRatio partial match
        ("AMILYA", "Amelia County", 51007),  # WRatio partial match
        ("aml", "Amelia County", 51007),  # WRatio partial match
        ("Nonexistent Place", "", 0),  # no match
        ("amelia county", "Amelia County", 51007),  # case-insensitive match
        ("Amelia County City", "Amelia County", 51007),  # extra words
        ("Buckingham", "Buckingham County", 51029),  # another partial match
        ("Danville", "Danville City", 51595),  # city match
        ("Suffolk", "Suffolk City", 51800),  # city match without suffix
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
        (0, "Monthly", True, 0),  # eligible, no income
        (0, "Annually", True, 0),  # eligible, no income
        (1200, "Monthly", True, 1200),  # eligible, monthly income below 300% of poverty
        (
            40000,
            "Annually",
            True,
            3333,
        ),  # eligible, yearly income below 300% of poverty
        (
            5000,
            "Monthly",
            False,
            5000,
        ),  # not eligible, monthly income above 300% of poverty
        (
            60000,
            "Annually",
            False,
            5000,
        ),  # not eligible, yearly income above 300% of poverty
        (3989, "Monthly", True, 3989),  # eligible, just below 300% poverty for month
        (47868, "Annually", True, 3989),  # eligible, just below 300% poverty for year
        (3990, "Monthly", True, 3990),  # eligible, at 300% poverty for month
        (47880, "Annually", True, 3990),  # eligible, at 300% poverty for year
        (
            3991,
            "Monthly",
            False,
            3991,
        ),  # not eligible, just over 300% poverty for month due to round()
        (
            47892,
            "Annually",
            False,
            3991,
        ),  # not eligible, just over 300% poverty for month due to round()
        (100000, "Annually", False, 8333),  # not eligible, high yearly income
        (10000, "Monthly", False, 10000),  # not eligible, high monthly income
        (1200, "Monthly", True, 1200),  # single member, low income
        (2400, "Monthly", True, 2400),  # single member, still eligible
        (3600, "Monthly", True, 3600),  # single member, just under
        (1200, "Annually", True, 100),  # very low yearly income
        (14400, "Annually", True, 1200),  # yearly, eligible
    ],
)
async def test_check_income(income, period, expected_eligible, expected_monthly_income):
    validator = IntakeValidator()
    income_detail = IncomeDetail(amount=income, period=IncomePeriod(period))
    member_income = MemberIncome({"Employment": income_detail})
    household_income = HouseholdIncome({"Test Person": member_income})
    is_eligible, monthly_income, household_size = await validator.check_income(
        income=household_income
    )
    assert is_eligible == expected_eligible
    assert monthly_income == expected_monthly_income
    assert household_size == 1


@pytest.mark.asyncio
async def test_check_income_weekly_period():
    """Test that weekly income is correctly converted to monthly (52 weeks / 12 months)."""
    validator = IntakeValidator()
    # 520/week = 2080/month = eligible
    income_detail = IncomeDetail(amount=520, period=IncomePeriod.WEEKLY)
    member_income = MemberIncome({"Employment": income_detail})
    household_income = HouseholdIncome({"Test Person": member_income})
    is_eligible, monthly_income, household_size = await validator.check_income(
        income=household_income
    )
    assert monthly_income == 2253  # (520 * 52) / 12 = 2253.33 -> 2253
    assert is_eligible is True
    assert household_size == 1


@pytest.mark.asyncio
async def test_check_income_biweekly_period():
    """Test that biweekly income is correctly converted to monthly (26 periods / 12 months)."""
    validator = IntakeValidator()
    # 1040/biweekly = 2253/month = eligible (note: 1040 * 26 / 12 = 2253.33)
    income_detail = IncomeDetail(amount=1040, period=IncomePeriod.BIWEEKLY)
    member_income = MemberIncome({"Employment": income_detail})
    household_income = HouseholdIncome({"Test Person": member_income})
    is_eligible, monthly_income, household_size = await validator.check_income(
        income=household_income
    )
    assert monthly_income == 2253  # (1040 * 26) / 12 = 2253.33 -> 2253
    assert is_eligible is True
    assert household_size == 1


@pytest.mark.asyncio
async def test_check_income_semi_monthly_period():
    """Test that semi-monthly income is correctly converted to monthly (2 periods = 1 month)."""
    validator = IntakeValidator()
    # 1200/semi-monthly = 2400/month = eligible
    income_detail = IncomeDetail(amount=1200, period=IncomePeriod.SEMI_MONTHLY)
    member_income = MemberIncome({"Employment": income_detail})
    household_income = HouseholdIncome({"Test Person": member_income})
    is_eligible, monthly_income, household_size = await validator.check_income(
        income=household_income
    )
    assert monthly_income == 2400  # 1200 * 2 = 2400
    assert is_eligible is True
    assert household_size == 1


@pytest.mark.asyncio
async def test_check_income_quarterly_period():
    """Test that quarterly income is correctly converted to monthly (4 quarters / 12 months)."""
    validator = IntakeValidator()
    # 6000/quarter = 2000/month = eligible
    income_detail = IncomeDetail(amount=6000, period=IncomePeriod.QUARTERLY)
    member_income = MemberIncome({"Employment": income_detail})
    household_income = HouseholdIncome({"Test Person": member_income})
    is_eligible, monthly_income, household_size = await validator.check_income(
        income=household_income
    )
    assert monthly_income == 2000  # (6000 * 4) / 12 = 2000
    assert is_eligible is True
    assert household_size == 1


@pytest.mark.asyncio
async def test_check_income_all_periods_mixed():
    """Test household with income from different periods."""
    validator = IntakeValidator()
    # Person 1: 2000/month
    income_detail_1 = IncomeDetail(amount=2000, period=IncomePeriod.MONTHLY)
    member_income_1 = MemberIncome({"Employment": income_detail_1})

    # Person 2: 24000/year = 2000/month
    income_detail_2 = IncomeDetail(amount=24000, period=IncomePeriod.ANNUALLY)
    member_income_2 = MemberIncome({"Child Support": income_detail_2})

    # Person 3: 520/week = 2253/month
    income_detail_3 = IncomeDetail(amount=520, period=IncomePeriod.WEEKLY)
    member_income_3 = MemberIncome({"Spousal Support": income_detail_3})

    household_income = HouseholdIncome(
        {
            "Person 1": member_income_1,
            "Person 2": member_income_2,
            "Person 3": member_income_3,
        }
    )
    is_eligible, monthly_income, household_size = await validator.check_income(
        income=household_income
    )
    # 2000 + 2000 + 2253 = 6253
    assert monthly_income == 6253
    assert (
        is_eligible is True
    )  # 6253 is below 300% poverty limit for 3-person household (which is ~6830)
    assert household_size == 3


@pytest.mark.asyncio
async def test_check_income_weekly_ineligible():
    """Test that weekly income can result in ineligibility."""
    validator = IntakeValidator()
    # 3850/week = 16683/month = ineligible
    income_detail = IncomeDetail(amount=3850, period=IncomePeriod.WEEKLY)
    member_income = MemberIncome({"Employment": income_detail})
    household_income = HouseholdIncome({"Test Person": member_income})
    is_eligible, monthly_income, household_size = await validator.check_income(
        income=household_income
    )
    assert monthly_income == 16683  # (3850 * 52) / 12 = 16683.33 -> 16683
    assert is_eligible is False
    assert household_size == 1


@pytest.mark.asyncio
async def test_check_income_semi_monthly_ineligible():
    """Test that semi-monthly income can result in ineligibility."""
    validator = IntakeValidator()
    # 2500/semi-monthly = 5000/month = ineligible
    income_detail = IncomeDetail(amount=2500, period=IncomePeriod.SEMI_MONTHLY)
    member_income = MemberIncome({"Employment": income_detail})
    household_income = HouseholdIncome({"Test Person": member_income})
    is_eligible, monthly_income, household_size = await validator.check_income(
        income=household_income
    )
    assert monthly_income == 5000  # 2500 * 2 = 5000
    assert is_eligible is False
    assert household_size == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "assets_data,expected_eligible,expected_value",
    [
        ([], True, 0),  # no assets
        ([{"primary car": 5000}], True, 0),  # explicit primary transportation is exempt
        ([{"car": 5000}], True, 5000),  # not all vehicles are exempt
        ([{"house": 10000}], True, 0),  # primary residence is exempt
        ([{"401k": 12000}], True, 0),  # retirement funds are exempt
        (
            [{"savings": 2000}, {"jewelry": 1500}],
            True,
            3500,
        ),  # countable assets below limit
        (
            [{"primary vehicle": 8000}, {"savings": 3000}, {"jewelry": 1500}],
            True,
            4500,
        ),  # explicit primary vehicle ignored
        ([{"car": 0}], True, 0),  # zero value countable asset
        (
            [{"boat": 0}, {"savings": 4999}],
            True,
            4999,
        ),  # just under limit with zero asset
        (
            [{"expensive_car": 15000}],
            False,
            15000,
        ),  # non-exempt asset name still counts
        (
            [
                {"savings": 2500},
                {"investments": 2500},
                {"jewelry": 2500},
                {"vacant land": 2500},
            ],
            True,
            10000,
        ),  # multiple assets exactly at limit
        ([{"vacant land": 10000}], True, 10000),  # single countable asset at limit
        ([{"vacant land": 9999}, {"cash": 1}], True, 10000),  # just at limit
        ([{"vacant land": 9999}, {"cash": 2}], False, 10001),  # just over limit
        (
            [{"car": -1000}, {"savings": 2000}],
            True,
            1000,
        ),  # countable vehicle still affects total
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
@pytest.mark.parametrize(
    "dob_input,expected_valid,expected_output",
    [
        ("01/15/1980", True, "1980-01-15"),  # MM/DD/YYYY format
        ("01-15-1980", True, "1980-01-15"),  # MM-DD-YYYY format
        ("1980-01-15", True, "1980-01-15"),  # ISO format
        ("January 15, 1980", True, "1980-01-15"),  # Month name format
        ("Jan 15, 1980", True, "1980-01-15"),  # Abbreviated month
        ("01/15/80", True, "1980-01-15"),  # 2-digit year
        ("12/31/1999", True, "1999-12-31"),  # end of century
        ("02/29/2000", True, "2000-02-29"),  # leap year
        ("invalid date", False, ""),  # invalid date string
        ("13/15/1980", False, ""),  # invalid month
        ("01/32/1980", False, ""),  # invalid day
        ("", False, ""),  # empty string
    ],
)
async def test_check_date_of_birth(dob_input, expected_valid, expected_output):
    validator = IntakeValidator()
    is_valid, formatted_dob = await validator.check_date_of_birth(dob_input)
    assert is_valid == expected_valid
    assert formatted_dob == expected_output


@pytest.mark.asyncio
async def test_check_date_of_birth_future_date():
    """Test that future dates are rejected."""
    from datetime import datetime, timedelta

    validator = IntakeValidator()
    future_date = (datetime.now() + timedelta(days=1)).strftime("%m/%d/%Y")
    is_valid, formatted_dob = await validator.check_date_of_birth(future_date)
    assert is_valid is False
    assert formatted_dob == ""


@pytest.mark.asyncio
async def test_check_date_of_birth_today():
    """Test that today's date is rejected (must be in the past)."""
    from datetime import datetime

    validator = IntakeValidator()
    today = datetime.now().strftime("%m/%d/%Y")
    is_valid, formatted_dob = await validator.check_date_of_birth(today)
    assert is_valid is False
    assert formatted_dob == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ssn_input,expected_valid,expected_formatted",
    [
        ("1234", True, "1234"),  # basic 4 digits
        ("123-4", True, "1234"),  # with one hyphen
        ("1-234", True, "1234"),  # with hyphen at different position
        ("1-2-3-4", True, "1234"),  # multiple hyphens
        ("1 2 3 4", True, "1234"),  # with spaces
        ("1_2_3_4", True, "1234"),  # with underscores
        ("1-2-34", True, "1234"),  # mixed separators
        ("9876", True, "9876"),  # different valid digits
        ("0000", True, "0000"),  # all zeros
        ("9999", True, "9999"),  # all nines
        ("123", False, ""),  # too short
        ("12345", False, ""),  # too long
        ("abcd", False, ""),  # letters only
        ("12-34a", False, ""),  # contains letter
        ("12-34!", False, ""),  # contains special character
        ("", False, ""),  # empty string
        ("   ", False, ""),  # only spaces
        ("1-2-3", False, ""),  # only 3 digits with separators
        ("12-34-567", False, ""),  # 5 digits
    ],
)
async def test_check_ssn_last_4(ssn_input, expected_valid, expected_formatted):
    validator = IntakeValidator()
    is_valid, formatted_ssn = await validator.check_ssn_last_4(ssn_input)
    assert is_valid == expected_valid
    assert formatted_ssn == expected_formatted
