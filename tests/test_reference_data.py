import pytest
from intake_bot.services.reference_data import ReferenceDataLoader
from unittest.mock import patch


@pytest.fixture(scope="module")
def loader():
    return ReferenceDataLoader()


def test_classifier_taxonomy_is_sorted_list_of_labels(loader):
    taxonomy = loader.classifier_taxonomy
    assert isinstance(taxonomy, list)
    assert len(taxonomy) > 0
    assert taxonomy == sorted(taxonomy)
    assert "Private Landlord/Tenant" in taxonomy


def test_label_for_legal_problem_code_by_full_entry(loader):
    label = loader.label_for_legal_problem_code("63 Private Landlord/Tenant")
    assert label == "Private Landlord/Tenant"


def test_label_for_legal_problem_code_by_numeric_prefix(loader):
    label = loader.label_for_legal_problem_code("63")
    assert label == "Private Landlord/Tenant"


def test_label_for_legal_problem_code_unknown(loader):
    label = loader.label_for_legal_problem_code("999")
    assert label is None


def test_label_for_legal_problem_code_empty(loader):
    label = loader.label_for_legal_problem_code("")
    assert label is None


def test_legal_problem_code_from_label(loader):
    code = loader.legal_problem_code_from_label("Private Landlord/Tenant")
    assert code == "63 Private Landlord/Tenant"


def test_legal_problem_code_from_label_unknown(loader):
    code = loader.legal_problem_code_from_label("Nonexistent Problem")
    assert code is None


@pytest.mark.parametrize(
    "location,expected_outcome,expected_name,expected_fips,expected_eligible",
    [
        # -- exact canonical --
        ("Amelia County", "exact_match", "Amelia County", 51007, True),
        ("Danville City", "exact_match", "Danville City", 51595, True),
        ("Franklin City", "exact_match", "Franklin City", 51630, True),
        ("Lynchburg City", "exact_match", "Lynchburg City", 51680, True),
        ("Suffolk City", "exact_match", "Suffolk City", 51800, True),
        ("Isle of Wight County", "exact_match", "Isle of Wight County", 51093, True),
        ("Prince Edward County", "exact_match", "Prince Edward County", 51147, True),
        # -- covered locality forms --
        ("Virginia Beach", "exact_match", "Virginia Beach City", 51810, True),
        ("Virginia Beach City", "exact_match", "Virginia Beach City", 51810, True),
        # -- bare names require confirmation --
        ("Amelia", "suggested", "Amelia County", None, None),
        ("Danville", "suggested", "Danville City", None, None),
        ("Emporia", "suggested", "Emporia City", None, None),
        ("Halifax", "suggested", "Halifax County", None, None),
        ("Isle of Wight", "suggested", "Isle of Wight County", None, None),
        ("Prince Edward", "suggested", "Prince Edward County", None, None),
        # -- wrapper patterns --
        ("Amelia County", "exact_match", "Amelia County", 51007, True),
        ("Prince Edward County", "exact_match", "Prince Edward County", 51147, True),
        ("Franklin City", "exact_match", "Franklin City", 51630, True),
        # -- out-of-state (full names) --
        ("North Carolina", "unserved", None, None, None),
        ("Tennessee", "unserved", None, None, None),
        ("Kentucky", "unserved", None, None, None),
        ("West Virginia", "unserved", None, None, None),
        ("Maryland", "unserved", None, None, None),
        ("District of Columbia", "unserved", None, None, None),
        # -- out-of-state (abbreviation at end) --
        ("Raleigh NC", "unserved", None, None, None),
        ("Nashville TN", "unserved", None, None, None),
        # -- out-of-state sentence --
        ("I live in South Carolina now", "unserved", None, None, None),
        ("We moved from Ohio", "unserved", None, None, None),
        # -- Franklin ambiguity --
        ("Franklin", "ambiguous", None, None, None),
        # -- unknown/unresolved --
        ("Nonexistent Place", "unresolved_service_area", None, None, None),
        ("", "unknown", None, None, None),
    ],
)
def test_resolve_service_area_real(
    loader, location, expected_outcome, expected_name, expected_fips, expected_eligible
):
    result = loader.resolve_service_area(location)
    assert result["outcome"] == expected_outcome, (
        f"Location '{location}': expected outcome {expected_outcome}, got {result['outcome']}"
    )
    assert result.get("canonical_name") == expected_name, (
        f"Location '{location}': expected canonical_name {expected_name!r}, got {result.get('canonical_name')!r}"
    )
    assert result.get("fips") == expected_fips, (
        f"Location '{location}': expected fips {expected_fips!r}, got {result.get('fips')!r}"
    )
    assert result.get("is_eligible") is expected_eligible, (
        f"Location '{location}': expected is_eligible {expected_eligible!r}, got {result.get('is_eligible')!r}"
    )


def test_ambiguous_franklin_candidates(loader):
    result = loader.resolve_service_area("Franklin")
    assert result["outcome"] == "ambiguous"
    assert set(result.get("candidates", [])) == {"Franklin City", "Franklin County"}


def test_franklin_city_resolves_exact(loader):
    result = loader.resolve_service_area("Franklin City")
    assert result["outcome"] == "exact_match"
    assert result["canonical_name"] == "Franklin City"
    assert result["fips"] == 51630
    assert result["is_eligible"] is True


def test_franklin_county_not_resolved(loader):
    result = loader.resolve_service_area("Franklin County")
    assert result["outcome"] not in ("exact_match", "unserved"), (
        f"Franklin County should not resolve as a configured locality, got {result['outcome']}"
    )
    # Fuzzy may suggest Franklin City; that fallback is consumed via retry/referral path
    # Not an exact/unserved match, so it will follow unresolved behavior


def test_abbreviation_position_sensitive(loader):
    assert loader.resolve_service_area("NC")["outcome"] == "unserved"
    assert loader.resolve_service_area("nc")["outcome"] == "unserved"
    assert loader.resolve_service_area("in Tennessee")["outcome"] == "unserved"
    assert loader.resolve_service_area("I am in NC")["outcome"] == "unserved"


def test_out_of_state_does_not_override_va_city(loader):
    assert loader.resolve_service_area("Danville VA")["outcome"] != "unserved"
    result = loader.resolve_service_area("Danville NC")
    assert result["outcome"] == "unserved"


def test_known_served_localities_all(loader):
    served = [n for n, i in loader.virginia_localities.items() if i["is_eligible"]]
    for name in served:
        result = loader.resolve_service_area(name)
        assert result["outcome"] in ("exact_match", "unserved"), (
            f"Served locality '{name}' not resolved"
        )
        assert result["fips"] is not None


def test_no_configured_locality_asserts_uncovered(loader):
    assert all(
        info["is_eligible"] is True for info in loader.virginia_localities.values()
    )


def test_official_name_normalizations_only(loader):
    assert set(loader.official_name_normalizations) == {"virginia beach"}
    for alias, canonical in loader.official_name_normalizations.items():
        result = loader.resolve_service_area(alias)
        assert result["outcome"] in ("exact_match", "unserved"), (
            f"Alias '{alias}' resolving to '{canonical}' got {result['outcome']}"
        )
        assert result["canonical_name"] == canonical


def test_no_whole_sentence_alias_match(loader):
    result = loader.resolve_service_area("I was in Amelia last week")
    assert result["outcome"] in ("unresolved_service_area", "unknown", "suggested"), (
        f"Whole-sentence 'Amelia' matched unexpectedly: {result['outcome']}"
    )


def test_out_of_state_with_punctuation(loader):
    assert loader.resolve_service_area("Danville, NC.")["outcome"] == "unserved"
    assert loader.resolve_service_area("Danville NC,")["outcome"] == "unserved"
    assert loader.resolve_service_area("Raleigh, NC!")["outcome"] == "unserved"


def test_va_suffix_resolves_city(loader):
    result = loader.resolve_service_area("Danville VA")
    assert result["outcome"] in ("exact_match", "suggested")
    assert result.get("canonical_name") == "Danville City"

    result2 = loader.resolve_service_area("Danville, Virginia")
    assert result2["outcome"] == "unresolved_service_area"
    assert result2.get("canonical_name") is None


def test_commonwealth_of_virginia_suffix_resolves_locality(loader):
    result = loader.resolve_service_area("Amelia County, Commonwealth of Virginia")

    assert result["outcome"] == "exact_match"
    assert result["canonical_name"] == "Amelia County"
    assert result["fips"] == 51007


def test_bare_virginia_does_not_suggest(loader):
    result = loader.resolve_service_area("Virginia")
    assert result["outcome"] == "unresolved_service_area"
    assert result.get("canonical_name") is None

    result2 = loader.resolve_service_area("va")
    assert result2["outcome"] == "unresolved_service_area"
    assert result2.get("canonical_name") is None


def test_direct_franklin_county_unresolved(loader):
    result = loader.resolve_service_area("Franklin County")
    assert result["outcome"] not in ("exact_match", "unserved"), (
        f"Franklin County must not resolve directly: {result['outcome']}"
    )
    # Must not fuzzy-suggest Franklin City
    assert result.get("canonical_name") is None
    assert result["outcome"] == "unresolved_service_area"


def test_three_strong_fuzzy_matches_return_top_two(loader):
    matches = [
        ("Amelia County", 95, 0),
        ("Amherst County", 90, 1),
        ("Arlington County", 85, 2),
    ]
    with patch(
        "rapidfuzz.process.extract",
        return_value=matches,
    ):
        result = loader.resolve_service_area("ambiguous place")

    assert result["outcome"] == "ambiguous"
    assert result["candidates"] == ["Amelia County", "Amherst County"]


def test_county_of_pattern(loader):
    result = loader.resolve_service_area("County of Amelia")
    assert result["outcome"] == "exact_match"
    assert result["canonical_name"] == "Amelia County"


def test_city_of_pattern(loader):
    result = loader.resolve_service_area("City of Danville")
    assert result["outcome"] == "exact_match"
    assert result["canonical_name"] == "Danville City"


def test_no_sentence_extraction(loader):
    result = loader.resolve_service_area(
        "The legal incident happened in Amelia County."
    )
    assert result["outcome"] not in ("exact_match", "unserved"), (
        f"Sentence must not resolve: {result['outcome']}"
    )


def test_no_extra_word_wrapper(loader):
    result = loader.resolve_service_area("Amelia County City")
    assert result["outcome"] not in ("exact_match", "unserved")
