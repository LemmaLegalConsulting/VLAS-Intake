import pytest
from intake_bot.models.validator import AdverseParty, CallerName, PhoneAdverseParty, PhoneTypeCaller


@pytest.mark.parametrize(
    "input_number,expected_formatted",
    [
        ("(866) 534-5243", "(866) 534-5243"),  # already formatted
        ("866-534-5243", "(866) 534-5243"),  # hyphen only
        ("8665345243", "(866) 534-5243"),  # digits only
        ("866.534.5243", "(866) 534-5243"),  # with dots
        ("866 534 5243", "(866) 534-5243"),  # with spaces
        ("+18665345243", "(866) 534-5243"),  # +1 prefix
        ("+1 (866) 534-5243", "(866) 534-5243"),  # international format with +1
        ("1-866-534-5243", "(866) 534-5243"),  # with leading 1
        ("(866)534-5243", "(866) 534-5243"),  # no space after parenthesis
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
