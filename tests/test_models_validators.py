import pytest
from pydantic import ValidationError

from intake_bot.models.validator import (
    Address,
    AdverseParty,
    CallerName,
    HouseholdIncome,
    IncomePeriod,
    PhoneAdverseParty,
    PhoneTypeCaller,
)


@pytest.mark.parametrize(
    "input_number,expected_formatted",
    [
        ("(866) 534-5243", "+18665345243"),  # E.164 format
        ("866-534-5243", "+18665345243"),  # hyphen only
        ("8665345243", "+18665345243"),  # digits only
        ("866.534.5243", "+18665345243"),  # with dots
        ("866 534 5243", "+18665345243"),  # with spaces
        ("+18665345243", "+18665345243"),  # +1 prefix
        ("+1 (866) 534-5243", "+18665345243"),  # international format with +1
        ("1-866-534-5243", "+18665345243"),  # with leading 1
        ("(866)534-5243", "+18665345243"),  # no space after parenthesis
    ],
)
def test_phone_number_validation_and_formatting(input_number, expected_formatted):
    """Test that Phone model validates and formats valid US phone numbers."""
    phone = PhoneAdverseParty(number=input_number, type=PhoneTypeCaller.MOBILE)
    assert phone.number == expected_formatted
    assert phone.type == PhoneTypeCaller.MOBILE


@pytest.mark.parametrize(
    "invalid_number",
    [
        "123-456-7890",  # invalid area code (can't start with "1")
        "abc-def-ghij",  # letters only
        "866534524",  # too short
        "",  # empty string
        "+44 20 7946 0958",  # non-US number
        "911",  # emergency number
        "000-000-0000",  # invalid number
        "(999) 999-9999",  # invalid area code
    ],
)
def test_phone_number_validation_rejects_invalid(invalid_number):
    """Test that Phone model rejects invalid phone numbers."""
    with pytest.raises(ValueError, match="Invalid US phone number"):
        PhoneAdverseParty(number=invalid_number, type=PhoneTypeCaller.MOBILE)


def test_caller_name_suffix_strips_and_keeps_value():
    name = CallerName(
        first=" John ", middle=" Q ", last=" Public ", suffix=" Jr. ", type="Legal Name"
    )
    assert name.first == "John"
    assert name.middle == "Q"
    assert name.last == "Public"
    assert name.suffix == "Jr."


def test_caller_name_suffix_empty_becomes_none():
    name = CallerName(first="John", last="Public", suffix="   ", type="Legal Name")
    assert name.suffix is None


def test_adverse_party_suffix_optional():
    party = AdverseParty(first="Bob", last="Smith", suffix="Sr.")
    assert party.suffix == "Sr."


def test_adverse_party_accepts_organization_without_person_fields():
    party = AdverseParty(organization_name="First National Bank")

    assert party.organization_name == "First National Bank"
    assert party.first is None
    assert party.last is None


def test_adverse_party_rejects_person_fields_for_organization():
    with pytest.raises(ValidationError, match="either organization_name"):
        AdverseParty(
            organization_name="First National Bank",
            first="First",
            last="National Bank",
        )


def test_adverse_party_rejects_organization_date_of_birth():
    with pytest.raises(ValidationError, match="Date of birth is only valid"):
        AdverseParty(organization_name="First National Bank", dob="1980-05-15")


@pytest.mark.parametrize(
    "party",
    [
        {"organization_name": "   "},
        {"first": " ", "last": " "},
    ],
)
def test_adverse_party_rejects_whitespace_only_names(party):
    with pytest.raises(ValidationError, match="Provide organization_name"):
        AdverseParty(**party)


@pytest.mark.parametrize(
    "raw_period,expected",
    [
        ("month", IncomePeriod.MONTHLY),
        ("Monthly", IncomePeriod.MONTHLY),
        ("bi-weekly", IncomePeriod.BIWEEKLY),
        ("semi monthly", IncomePeriod.SEMI_MONTHLY),
        ("year", IncomePeriod.ANNUALLY),
        (12, IncomePeriod.MONTHLY),
        (52, IncomePeriod.WEEKLY),
    ],
)
def test_income_period_aliases_normalize(raw_period, expected):
    income = HouseholdIncome.model_validate(
        {
            "Jack Adamson": {
                "Employment": {
                    "amount": 100000,
                    "period": raw_period,
                }
            }
        }
    )
    assert income.root["Jack Adamson"].root["Employment"].period == expected


def test_household_income_empty_listing_normalizes_to_no_household_income():
    income = HouseholdIncome.model_validate({})

    # Ensure we create a single explicit "no income" entry
    assert "Household" in income.root
    assert "No Household Income" in income.root["Household"].root
    detail = income.root["Household"].root["No Household Income"]
    assert detail.amount == 0
    assert detail.period == IncomePeriod.MONTHLY


class TestAddressModel:
    def test_address_county_strip_county(self):
        """Test that 'County' suffix is stripped from county field."""
        data = {
            "street": "123 Main St",
            "city": "Richmond",
            "state": "VA",
            "zip": "23219",
            "county": "Amelia County",
        }
        address = Address(**data)
        assert address.county == "Amelia"

    def test_address_county_no_suffix_change(self):
        """Test that county without 'County' suffix is unchanged."""
        data = {
            "street": "123 Main St",
            "city": "Richmond",
            "state": "VA",
            "zip": "23219",
            "county": "Amelia",
        }
        address = Address(**data)
        assert address.county == "Amelia"

    def test_address_county_case_insensitive_strip(self):
        """Test that 'County' suffix stripping is case insensitive."""
        data = {
            "street": "123 Main St",
            "city": "Richmond",
            "state": "VA",
            "zip": "23219",
            "county": "Amelia county",
        }
        address = Address(**data)
        assert address.county == "Amelia"

    def test_address_county_strip_whitespace(self):
        """Test that whitespace is handled correctly when stripping."""
        data = {
            "street": "123 Main St",
            "city": "Richmond",
            "state": "VA",
            "zip": "23219",
            "county": " Amelia County ",
        }
        address = Address(**data)
        assert address.county == "Amelia"

    def test_asset_entry_rejects_boolean(self):
        from intake_bot.models.validator import AssetEntry

        with pytest.raises(ValueError, match="not a boolean"):
            AssetEntry.model_validate({"car": True})

    def test_asset_entry_rejects_string(self):
        from intake_bot.models.validator import AssetEntry

        with pytest.raises(ValueError, match="not a string"):
            AssetEntry.model_validate({"car": "five thousand"})

    def test_asset_entry_rejects_negative(self):
        from intake_bot.models.validator import AssetEntry

        with pytest.raises(ValueError, match="non-negative"):
            AssetEntry.model_validate({"car": -100})

    def test_asset_entry_rejects_overflow(self):
        from intake_bot.models.validator import AssetEntry

        with pytest.raises(ValueError, match="Unreasonable"):
            AssetEntry.model_validate({"car": 100_000_001})

    def test_asset_entry_rejects_integral_float(self):
        from intake_bot.models.validator import AssetEntry

        with pytest.raises(ValueError, match="not a float"):
            AssetEntry.model_validate({"car": 5000.0})

    def test_income_detail_rejects_boolean(self):
        from intake_bot.models.validator import IncomeDetail

        with pytest.raises(ValueError, match="not a boolean"):
            IncomeDetail(amount=True, period="Monthly")

    def test_income_detail_rejects_string(self):
        from intake_bot.models.validator import IncomeDetail

        with pytest.raises(ValueError, match="not a string"):
            IncomeDetail(amount="five thousand", period="Monthly")

    def test_income_detail_rejects_negative(self):
        from intake_bot.models.validator import IncomeDetail

        with pytest.raises(ValueError, match="non-negative"):
            IncomeDetail(amount=-100, period="Monthly")

    def test_income_detail_rejects_overflow(self):
        from intake_bot.models.validator import IncomeDetail

        with pytest.raises(ValueError, match="Unreasonable"):
            IncomeDetail(amount=200_000_000, period="Monthly")

    def test_asset_collection_validates_all_entries(self):
        from intake_bot.models.validator import AssetEntry, Assets

        with pytest.raises(ValueError, match="not a boolean"):
            Assets([AssetEntry({"car": 5000}), AssetEntry({"cash": True})])

    def test_asset_validate_rejects_bad_value(self):
        from intake_bot.nodes.validator import IntakeValidator

        with pytest.raises(ValueError, match="not a"):
            IntakeValidator.assets_validate([{"car": "big"}])

    def test_valid_then_invalid_income_state_preserved(self):
        from intake_bot.models.validator import HouseholdIncome

        valid = HouseholdIncome.model_validate(
            {"Person": {"wages": {"amount": 1000, "period": "Monthly"}}}
        )
        assert valid.root["Person"].root["wages"].amount == 1000

    def test_float_income_amount_is_rejected(self):
        from intake_bot.models.validator import IncomeDetail

        with pytest.raises(ValueError, match="not a float"):
            IncomeDetail(amount=5000.0, period="Monthly")

    def test_asset_entry_accepts_zero(self):
        from intake_bot.models.validator import AssetEntry, Assets

        entry = AssetEntry({"car": 0})
        assets = Assets([entry])
        assert assets.root[0].root["car"] == 0

    def test_asset_entry_accepts_max_value(self):
        from intake_bot.models.validator import AssetEntry

        entry = AssetEntry({"car": 100_000_000})
        assert entry.root["car"] == 100_000_000

    def test_asset_entry_rejects_max_plus_one(self):
        from intake_bot.models.validator import AssetEntry

        with pytest.raises(ValueError, match="Unreasonable"):
            AssetEntry.model_validate({"car": 100_000_001})

    def test_validation_before_exemption_filter(self):
        from intake_bot.nodes.validator import IntakeValidator

        # Validation via assets_validate rejects malformed values
        with pytest.raises(ValueError, match="not a"):
            IntakeValidator.assets_validate([{"home": "big"}])

    def test_address_validation_required_fields(self):
        """Test that required fields are validated."""
        data = {
            "street": " ",  # Empty/whitespace
            "city": "Richmond",
            "state": "VA",
            "zip": "23219",
            "county": "Amelia",
        }
        with pytest.raises(ValidationError):
            Address(**data)
