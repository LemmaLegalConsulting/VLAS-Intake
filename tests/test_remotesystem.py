import pytest
from intake_bot.intake_nodes import MockRemoteSystem  # type: ignore


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_area,expected_matches",
    [
        ("Amelia County", ["Amelia County"]),  # exact match
        ("Amelia", ["Amelia County"]),  # partial match
        ("22951", [22951]),  # zip code match
        ("Nonexistent Place", []),  # no match
        ("amelia county", ["Amelia County"]),  # case-insensitive match
        ("Amelia County City", ["Amelia County"]),  # extra words
    ],
)
async def test_check_service_area_matches(user_area, expected_matches):
    remote = MockRemoteSystem()
    matches = await remote.check_service_area(user_area)
    # Convert all to str for comparison, since zip codes are int
    matches_str = [str(m) for m in matches]
    expected_str = [str(m) for m in expected_matches]
    for e in expected_str:
        assert e in matches_str
    assert len(matches_str) == len(expected_str)


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
