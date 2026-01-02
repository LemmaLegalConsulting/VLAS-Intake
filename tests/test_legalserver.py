from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from intake_bot.models.validator import NameTypeValue
from intake_bot.services.legalserver import (
    _build_matter_payload,
    _save_additional_names,
    _save_adverse_parties,
    _save_assets_note,
    _save_case_description_note,
    _save_income_records,
)


class TestBuildMatterPayload:
    """Tests for _build_matter_payload helper function."""

    def test_basic_payload_with_required_fields(self):
        """Test building payload with minimum required fields."""
        state = {
            "names": {
                "names": [
                    {
                        "first": "John",
                        "last": "Doe",
                        "middle": "Michael",
                        "suffix": None,
                    }
                ]
            }
        }

        payload = _build_matter_payload(state)

        assert payload["first"] == "John"
        assert payload["last"] == "Doe"
        assert payload["middle"] == "Michael"
        assert payload["case_disposition"] == "Rejected"
        assert payload["rejection_reason"]["lookup_value_name"] == "Other"
        assert "suffix" not in payload  # None values excluded

    def test_payload_with_phone_number(self):
        """Test that valid phone number is included."""
        state = {
            "names": {"names": [{"first": "Jane", "last": "Smith"}]},
            "phone": {"is_valid": True, "phone_number": "(866) 534-5243"},
        }

        payload = _build_matter_payload(state)

        assert payload["mobile_phone"] == "(866) 534-5243"

    def test_payload_sets_client_legal_name_custom_field(self):
        """Test that the custom matter field for legal name is set when primary name type is Legal Name."""
        state = {
            "names": {
                "names": [
                    {
                        "first": "Jane",
                        "last": "Smith",
                        "type": "Legal Name",
                    }
                ]
            }
        }

        payload = _build_matter_payload(state)

        assert payload["custom_fields"]["is_this_the_client_s_legal_name__1065"] is True

    def test_payload_with_legal_problem_code(self):
        """Test that legal problem code is included."""
        state = {
            "names": {"names": [{"first": "Alice", "last": "Johnson"}]},
            "case_type": {
                "is_eligible": True,
                "legal_problem_code": "32 Divorce/Sep./Annul.",
            },
        }

        payload = _build_matter_payload(state)

        assert payload["legal_problem_code"] == "32 Divorce/Sep./Annul."

    def test_payload_with_county_of_dispute(self):
        """Test that service area FIPS code is included as county_of_dispute."""
        state = {
            "names": {"names": [{"first": "Bob", "last": "Wilson"}]},
            "service_area": {"location": "Amelia County", "is_eligible": True, "fips_code": 51007},
        }

        payload = _build_matter_payload(state)

        assert payload["county_of_dispute"] == {
            "county_FIPS": "51007",
        }
        assert "county_of_residence" not in payload

    def test_payload_with_income_eligibility(self):
        """Test that income eligibility flag is included."""
        state = {
            "names": {"names": [{"first": "Carol", "last": "Brown"}]},
            "income": {"is_eligible": True, "monthly_amount": 2000, "household_size": 3},
        }

        payload = _build_matter_payload(state)

        assert payload["income_eligible"] is True
        assert payload["number_of_adults"] == 3

    def test_payload_with_household_composition(self):
        """Test that household composition is properly mapped to LegalServer fields."""
        state = {
            "names": {"names": [{"first": "Alice", "last": "Johnson"}]},
            "household_composition": {
                "number_of_adults": 2,
                "number_of_children": 3,
            },
        }

        payload = _build_matter_payload(state)

        assert payload["number_of_adults"] == 2
        assert payload["number_of_children"] == 3

    def test_payload_household_composition_overrides_income_household_size(self):
        """Test that household_composition takes precedence over income household_size for number_of_adults."""
        state = {
            "names": {"names": [{"first": "Betty", "last": "Davis"}]},
            "income": {"is_eligible": True, "monthly_amount": 2000, "household_size": 5},
            "household_composition": {
                "number_of_adults": 2,
                "number_of_children": 3,
            },
        }

        payload = _build_matter_payload(state)

        assert payload["income_eligible"] is True
        # household_composition should override the income household_size
        assert payload["number_of_adults"] == 2
        assert payload["number_of_children"] == 3

    def test_payload_with_asset_eligibility(self):
        """Test that asset eligibility flag is included."""
        state = {
            "names": {"names": [{"first": "David", "last": "Taylor"}]},
            "assets": {"is_eligible": False, "total_value": 5000},
        }

        payload = _build_matter_payload(state)

        assert payload["asset_eligible"] is False

    def test_payload_with_citizenship_true(self):
        """Test that US citizenship is mapped correctly."""
        state = {
            "names": {"names": [{"first": "Eva", "last": "Martinez"}]},
            "citizenship": {"is_citizen": True},
        }

        payload = _build_matter_payload(state)

        assert payload["citizenship"] == "Citizen"

    def test_payload_with_citizenship_false(self):
        """Test that non-US citizenship is mapped correctly."""
        state = {
            "names": {"names": [{"first": "Frank", "last": "Garcia"}]},
            "citizenship": {"is_citizen": False},
        }

        payload = _build_matter_payload(state)

        assert payload["citizenship"] == "Non-Citizen"

    def test_payload_with_domestic_violence(self):
        """Test that domestic violence flag is included."""
        state = {
            "names": {"names": [{"first": "Grace", "last": "Lee"}]},
            "domestic_violence": {"is_experiencing": True},
        }

        payload = _build_matter_payload(state)

        assert payload["victim_of_domestic_violence"] is True

    def test_payload_without_domestic_violence(self):
        """Test that domestic violence flag is false when not experiencing."""
        state = {
            "names": {"names": [{"first": "Henry", "last": "Zhang"}]},
            "domestic_violence": {"is_experiencing": False},
        }

        payload = _build_matter_payload(state)

        assert payload["victim_of_domestic_violence"] is False

    def test_payload_with_date_of_birth(self):
        """Test that date of birth is included in payload."""
        state = {
            "names": {"names": [{"first": "Isabel", "last": "Martinez"}]},
            "date_of_birth": {"date_of_birth": "1990-05-15"},
        }

        payload = _build_matter_payload(state)

        assert payload["date_of_birth"] == "1990-05-15"

    def test_payload_with_date_of_birth_iso_format(self):
        """Test that date of birth is serialized as ISO format string."""
        state = {
            "names": {"names": [{"first": "Jack", "last": "Wilson"}]},
            "date_of_birth": {"date_of_birth": "1985-12-25"},
        }

        payload = _build_matter_payload(state)

        # Pydantic converts date objects to ISO format strings with mode='json'
        assert payload["date_of_birth"] == "1985-12-25"
        assert isinstance(payload["date_of_birth"], str)

    def test_payload_excludes_none_date_of_birth(self):
        """Test that None date_of_birth is excluded from payload."""
        state = {
            "names": {"names": [{"first": "Kate", "last": "Johnson"}]},
            "date_of_birth": {"date_of_birth": None},
        }

        payload = _build_matter_payload(state)

        assert "date_of_birth" not in payload

    def test_payload_excludes_empty_string_date_of_birth(self):
        """Test that empty string date_of_birth causes validation failure."""
        state = {
            "names": {"names": [{"first": "Leo", "last": "Brown"}]},
            "date_of_birth": {"date_of_birth": ""},
        }

        # Empty string should fail validation and return None
        payload = _build_matter_payload(state)
        assert payload is None

    def test_payload_excludes_missing_date_of_birth_section(self):
        """Test that missing date_of_birth section is handled gracefully."""
        state = {
            "names": {"names": [{"first": "Mike", "last": "Davis"}]},
        }

        payload = _build_matter_payload(state)

        assert "date_of_birth" not in payload

    def test_payload_with_address_full(self):
        """Test that full address is included in payload."""
        state = {
            "names": {"names": [{"first": "Robert", "last": "Taylor"}]},
            "address": {
                "address": {
                    "street": "123 Main Street",
                    "street_2": "Apt 4B",
                    "city": "Richmond",
                    "state": "VA",
                    "zip": "23219",
                    "county": "Arlington",
                }
            },
        }

        payload = _build_matter_payload(state)

        assert payload["home_street"] == "123 Main Street"
        assert payload["home_apt_num"] == "Apt 4B"
        assert payload["home_city"] == "Richmond"
        assert payload["home_state"] == "VA"
        assert payload["home_zip"] == "23219"
        assert payload["county_of_residence"] == {"county_name": "Arlington", "county_state": "VA"}

    def test_payload_with_address_without_apartment(self):
        """Test that address without apartment number is included."""
        state = {
            "names": {"names": [{"first": "Patricia", "last": "Garcia"}]},
            "address": {
                "address": {
                    "street": "456 Oak Avenue",
                    "street_2": None,
                    "city": "Arlington",
                    "state": "VA",
                    "zip": "22201",
                    "county": "Arlington",
                }
            },
        }

        payload = _build_matter_payload(state)

        assert payload["home_street"] == "456 Oak Avenue"
        assert "home_apt_num" not in payload  # None values excluded
        assert payload["home_city"] == "Arlington"
        assert payload["home_state"] == "VA"
        assert payload["home_zip"] == "22201"
        assert payload["county_of_residence"] == {"county_name": "Arlington", "county_state": "VA"}

    def test_payload_excludes_missing_address_section(self):
        """Test that missing address section is handled gracefully."""
        state = {
            "names": {"names": [{"first": "Nancy", "last": "White"}]},
        }

        payload = _build_matter_payload(state)

        assert "home_street" not in payload
        assert "home_apt_num" not in payload
        assert "home_city" not in payload
        assert "home_state" not in payload
        assert "home_zip" not in payload

    def test_complete_payload_with_date_of_birth_and_all_fields(self):
        """Test building complete payload with date of birth and all other fields."""
        state = {
            "call_id": "test-call-123",
            "phone": {"is_valid": True, "phone_number": "(703) 555-1234"},
            "names": {
                "names": [
                    {
                        "first": "Sarah",
                        "middle": "Jane",
                        "last": "Anderson",
                        "suffix": "Jr.",
                    }
                ]
            },
            "date_of_birth": {"date_of_birth": "1975-03-20"},
            "address": {
                "address": {
                    "street": "789 Elm Road",
                    "street_2": "Suite 100",
                    "city": "Alexandria",
                    "state": "VA",
                    "zip": "22314",
                    "county": "Arlington",
                }
            },
            "service_area": {
                "location": "Arlington County, VA",
                "is_eligible": True,
                "fips_code": 51013,
            },
            "case_type": {
                "is_eligible": True,
                "legal_problem_code": "42 Family Law/Domestic Relations",
            },
            "income": {"is_eligible": True, "monthly_amount": 3000, "household_size": 2},
            "assets": {"is_eligible": True, "total_value": 0},
            "citizenship": {"is_citizen": True},
            "domestic_violence": {
                "is_experiencing": False,
            },
            "emergency": {"is_emergency": False},
        }

        payload = _build_matter_payload(state)

        assert payload["first"] == "Sarah"
        assert payload["middle"] == "Jane"
        assert payload["last"] == "Anderson"
        assert payload["suffix"] == "Jr."

        assert payload["date_of_birth"] == "1975-03-20"
        assert payload["home_street"] == "789 Elm Road"
        assert payload["home_apt_num"] == "Suite 100"
        assert payload["home_city"] == "Alexandria"
        assert payload["home_state"] == "VA"
        assert payload["home_zip"] == "22314"
        assert payload["mobile_phone"] == "(703) 555-1234"
        assert payload["legal_problem_code"] == "42 Family Law/Domestic Relations"
        assert payload["county_of_dispute"]["county_FIPS"] == "51013"
        assert payload["county_of_residence"] == {"county_name": "Arlington", "county_state": "VA"}
        assert payload["income_eligible"] is True
        assert payload["asset_eligible"] is True
        assert payload["citizenship"] == "Citizen"
        assert payload["victim_of_domestic_violence"] is False
        assert payload["case_disposition"] == "Incomplete Intake"

    def test_payload_excludes_none_values(self):
        """Test that None values are excluded from payload."""
        state = {
            "names": {"names": [{"first": "Iris", "last": "Kim", "middle": None}]},
        }

        payload = _build_matter_payload(state)

        assert "middle" not in payload
        assert payload["first"] == "Iris"
        assert payload["last"] == "Kim"

    def test_payload_with_missing_names_section(self):
        """Test payload handles missing names gracefully."""
        state = {}

        payload = _build_matter_payload(state)

        assert payload is None

    def test_payload_with_empty_names_list(self):
        """Test payload handles empty names list gracefully."""
        state = {"names": {"names": []}}

        payload = _build_matter_payload(state)

        assert payload is None

    def test_complete_payload_with_all_fields(self):
        """Test building complete payload with all fields populated."""
        state = {
            "call_id": "test-call-123",
            "phone": {"is_valid": True, "phone_number": "(703) 555-1234"},
            "names": {
                "names": [
                    {
                        "first": "Sarah",
                        "middle": "Jane",
                        "last": "Anderson",
                        "suffix": "Jr.",
                    }
                ]
            },
            "service_area": {
                "location": "Arlington County, VA",
                "is_eligible": True,
                "fips_code": 51013,
            },
            "case_type": {
                "is_eligible": True,
                "legal_problem_code": "42 Family Law/Domestic Relations",
            },
            "income": {"is_eligible": True, "monthly_amount": 3000, "household_size": 2},
            "assets": {"is_eligible": True, "total_value": 0},
            "citizenship": {"is_citizen": True},
            "domestic_violence": {
                "is_experiencing": False,
            },
            "emergency": {"is_emergency": False},
        }

        payload = _build_matter_payload(state)

        assert payload["first"] == "Sarah"
        assert payload["middle"] == "Jane"
        assert payload["last"] == "Anderson"
        assert payload["suffix"] == "Jr."
        assert payload["mobile_phone"] == "(703) 555-1234"
        assert payload["legal_problem_code"] == "42 Family Law/Domestic Relations"
        assert payload["county_of_dispute"]["county_FIPS"] == "51013"
        assert "county_of_residence" not in payload
        assert payload["income_eligible"] is True
        assert payload["asset_eligible"] is True
        assert payload["citizenship"] == "Citizen"
        assert payload["victim_of_domestic_violence"] is False
        assert payload["case_disposition"] == "Incomplete Intake"

    def test_payload_with_ssn_last_4(self):
        """Test that SSN last 4 is included in payload."""
        state = {
            "names": {"names": [{"first": "Bob", "last": "Smith"}]},
            "ssn_last_4": {"ssn_last_4": "5678"},
        }

        payload = _build_matter_payload(state)

        assert payload["ssn"] == "5678"

    def test_payload_with_ssn_last_4_various_formats(self):
        """Test that SSN last 4 with separators is properly cleaned."""
        state = {
            "names": {"names": [{"first": "Carol", "last": "White"}]},
            "ssn_last_4": {"ssn_last_4": "1234"},  # Already cleaned by validator
        }

        payload = _build_matter_payload(state)

        assert payload["ssn"] == "1234"

    def test_payload_excludes_empty_ssn_last_4(self):
        """Test that empty SSN last 4 is handled (kept as empty string by Pydantic)."""
        state = {
            "names": {"names": [{"first": "David", "last": "Jones"}]},
            "ssn_last_4": {"ssn_last_4": ""},
        }

        payload = _build_matter_payload(state)

        # Empty strings are preserved by Pydantic, not excluded by exclude_none
        # (exclude_none only excludes None values, not empty strings)
        # The validation should have rejected this before reaching here in real usage
        assert payload.get("ssn") == "" or "ssn" not in payload

    def test_payload_excludes_missing_ssn_last_4_section(self):
        """Test payload when SSN section is missing."""
        state = {
            "names": {"names": [{"first": "Eve", "last": "Brown"}]},
        }

        payload = _build_matter_payload(state)

        assert "ssn" not in payload or payload.get("ssn") is None

    def test_complete_payload_with_ssn_last_4_and_all_fields(self):
        """Test building complete payload with SSN last 4 and all other fields."""

        state = {
            "call_id": "test-call-456",
            "phone": {"is_valid": True, "phone_number": "(571) 555-9999"},
            "names": {
                "names": [
                    {
                        "first": "Michael",
                        "middle": "James",
                        "last": "Davies",
                        "suffix": "Sr.",
                    }
                ]
            },
            "ssn_last_4": {"ssn_last_4": "8765"},
            "date_of_birth": {"date_of_birth": "1980-08-10"},
            "service_area": {
                "location": "Fairfax County, VA",
                "is_eligible": True,
                "fips_code": 51059,
            },
            "case_type": {
                "is_eligible": True,
                "legal_problem_code": "91 Consumer Transactions",
            },
            "income": {"is_eligible": True, "monthly_amount": 2500, "household_size": 1},
            "assets": {"is_eligible": True, "total_value": 5000},
            "citizenship": {"is_citizen": True},
            "domestic_violence": {
                "is_experiencing": False,
            },
        }

        payload = _build_matter_payload(state)

        assert payload["first"] == "Michael"
        assert payload["middle"] == "James"
        assert payload["last"] == "Davies"
        assert payload["suffix"] == "Sr."
        assert payload["ssn"] == "8765"
        assert payload["date_of_birth"] == "1980-08-10"
        assert payload["mobile_phone"] == "(571) 555-9999"
        assert payload["legal_problem_code"] == "91 Consumer Transactions"
        assert payload["county_of_dispute"]["county_FIPS"] == "51059"
        assert "county_of_residence" not in payload
        assert payload["income_eligible"] is True
        assert payload["asset_eligible"] is True
        assert payload["citizenship"] == "Citizen"
        assert payload["victim_of_domestic_violence"] is False


@pytest.mark.asyncio
class TestSaveIncomeRecords:
    """Tests for _save_income_records helper function."""

    async def test_save_single_income_record(self):
        """Test saving a single income record."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            return_value=MagicMock(status_code=201, json=MagicMock(return_value={}))
        )

        income_data = {
            "is_eligible": True,
            "listing": {"John Doe": {"Employment": {"amount": 50000, "period": "Annually"}}},
        }

        await _save_income_records(mock_client, "test-uuid-123", income_data)

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "test-uuid-123" in call_args[0][0]
        assert call_args[1]["json"]["type"] == {"lookup_value_name": "Employment"}
        assert call_args[1]["json"]["amount"] == 50000
        assert call_args[1]["json"]["period"] == "Annually"

    async def test_save_multiple_income_records(self):
        """Test saving multiple income records for different household members."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        income_data = {
            "is_eligible": True,
            "listing": {
                "John Doe": {"Employment": {"amount": 50000, "period": "Annually"}},
                "Jane Doe": {"Employment": {"amount": 60000, "period": "Annually"}},
            },
        }

        await _save_income_records(mock_client, "test-uuid-456", income_data)

        assert mock_client.post.call_count == 2

    async def test_save_income_with_different_periods(self):
        """Test that different period formats are valid LegalServer values."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        income_data = {
            "listing": {
                "Person A": {"Employment": {"amount": 5000, "period": "Monthly"}},
                "Person B": {"Unemployment Compensation": {"amount": 1000, "period": "Weekly"}},
                "Person C": {"Child Support": {"amount": 2000, "period": "Biweekly"}},
            }
        }

        await _save_income_records(mock_client, "test-uuid-789", income_data)

        # Check all three calls were made with correct periods
        calls = mock_client.post.call_args_list
        assert len(calls) == 3

        # Verify period values are passed through as-is
        periods = [call[1]["json"]["period"] for call in calls]
        assert "Monthly" in periods
        assert "Weekly" in periods
        assert "Biweekly" in periods

    async def test_skip_empty_household_member_records(self):
        """Test that empty income records are skipped."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        income_data = {
            "listing": {
                "John Doe": {"Employment": {"amount": 50000, "period": "Annually"}},
                "Jane Doe": {},  # Empty record
                "Child": None,  # None record
            }
        }

        await _save_income_records(mock_client, "test-uuid-skip", income_data)

        # Should only call once for John Doe
        assert mock_client.post.call_count == 1

    async def test_skip_records_with_missing_amount(self):
        """Test that records without amount are skipped."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        income_data = {
            "listing": {
                "John Doe": {
                    "Employment": {"amount": 50000, "period": "Annually"},
                    "Pension/Retirement (Not Soc. Sec.)": {"period": "Annually"},  # Missing amount
                }
            }
        }

        await _save_income_records(mock_client, "test-uuid-missing", income_data)

        # Should only call once for the income with amount
        assert mock_client.post.call_count == 1

    async def test_accept_zero_income_with_period(self):
        """Test that amount=0 is accepted (not treated as missing)."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        income_data = {
            "listing": {
                "John Doe": {
                    "Employment": {"amount": 0, "period": "Monthly"},  # Zero income
                    "Pension/Retirement (Not Soc. Sec.)": {
                        "period": "Monthly"
                    },  # Missing amount - should skip
                }
            }
        }

        await _save_income_records(mock_client, "test-uuid-zero", income_data)

        # Should only call once for amount=0 (which is valid), skip the missing amount
        assert mock_client.post.call_count == 1
        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["amount"] == 0
        assert call_args[1]["json"]["period"] == "Monthly"

    async def test_handle_non_dict_income_data(self):
        """Test handling of non-dict income data."""
        mock_client = AsyncMock()

        # Should not raise an error
        await _save_income_records(mock_client, "test-uuid", "invalid-data")

        # Should not attempt to post
        mock_client.post.assert_not_called()

    async def test_handle_missing_listing(self):
        """Test handling of income data without listing."""
        mock_client = AsyncMock()

        income_data = {"is_eligible": True}  # No listing

        await _save_income_records(mock_client, "test-uuid", income_data)

        # Should not attempt to post
        mock_client.post.assert_not_called()

    async def test_log_failed_income_record_creation(self):
        """Test that failed income record creation is logged."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=400, reason="Bad Request"))

        income_data = {
            "listing": {"John Doe": {"Employment": {"amount": 50000, "period": "Annually"}}}
        }

        with patch("intake_bot.services.legalserver.logger") as mock_logger:
            await _save_income_records(mock_client, "test-uuid", income_data)
            mock_logger.warning.assert_called()

    async def test_capitalize_income_type(self):
        """Test that income category IDs are properly handled."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        income_data = {
            "listing": {
                "Person": {
                    "Employment": {"amount": 5000, "period": "Monthly"},
                    "Child Support": {"amount": 500, "period": "Monthly"},
                }
            }
        }

        await _save_income_records(mock_client, "test-uuid", income_data)

        calls = mock_client.post.call_args_list
        types = [call[1]["json"]["type"] for call in calls]
        assert {"lookup_value_name": "Employment"} in types
        assert {"lookup_value_name": "Child Support"} in types

    async def test_handle_exception_in_save_income_records(self):
        """Test that exceptions in save_income_records are handled gracefully."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Connection error"))

        income_data = {
            "listing": {"John Doe": {"Employment": {"amount": 50000, "period": "Annually"}}}
        }

        with patch("intake_bot.services.legalserver.logger") as mock_logger:
            # Should not raise
            await _save_income_records(mock_client, "test-uuid", income_data)
            mock_logger.error.assert_called()


@pytest.mark.asyncio
class TestSaveAdditionalNames:
    """Tests for _save_additional_names helper function."""

    async def test_save_single_additional_name(self):
        """Test saving a single additional name via API."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        names_list = [
            {
                "first": "John",
                "middle": "Michael",
                "last": "Doe",
                "suffix": None,
                "type": NameTypeValue.LEGAL_NAME,
            },
            {
                "first": "Jane",
                "middle": "Marie",
                "last": "Smith",
                "suffix": None,
                "type": NameTypeValue.MAIDEN_NAME,
            },
        ]

        await _save_additional_names(mock_client, "test-uuid-123", names_list)

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "test-uuid-123/additional_names" in call_args[0][0]
        assert call_args[1]["json"]["first"] == "Jane"
        assert call_args[1]["json"]["middle"] == "Marie"
        assert call_args[1]["json"]["last"] == "Smith"
        assert "suffix" not in call_args[1]["json"]  # None values excluded
        assert call_args[1]["json"]["type"]["lookup_value_name"] == NameTypeValue.MAIDEN_NAME.value

    async def test_save_additional_name_with_default_type(self):
        """Test that type defaults to Former Name when not specified."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        names_list = [
            {"first": "John", "last": "Doe"},
            {
                "first": "Jane",
                "last": "Smith",
            },  # No type specified, should default to FORMER_NAME
        ]

        await _save_additional_names(mock_client, "test-uuid-123", names_list)

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["type"]["lookup_value_name"] == NameTypeValue.FORMER_NAME.value

    async def test_save_multiple_additional_names(self):
        """Test saving multiple additional names via API with different types."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        names_list = [
            {"first": "John", "last": "Doe", "type": NameTypeValue.LEGAL_NAME},
            {"first": "Jane", "last": "Smith", "type": NameTypeValue.MAIDEN_NAME},
            {"first": "Bob", "last": "Johnson", "type": NameTypeValue.FORMER_NAME},
            {"first": "Alice", "last": "Williams"},  # No type, defaults to FORMER_NAME
        ]

        await _save_additional_names(mock_client, "test-uuid-456", names_list)

        # Should call once for each additional name (3 total)
        assert mock_client.post.call_count == 3

        # Check each call has the correct type
        calls = mock_client.post.call_args_list
        assert calls[0][1]["json"]["type"]["lookup_value_name"] == NameTypeValue.MAIDEN_NAME.value
        assert calls[1][1]["json"]["type"]["lookup_value_name"] == NameTypeValue.FORMER_NAME.value
        assert calls[2][1]["json"]["type"]["lookup_value_name"] == NameTypeValue.FORMER_NAME.value

    async def test_skip_when_only_primary_name(self):
        """Test that no API call is made when only primary name exists."""
        mock_client = AsyncMock()

        names_list = [{"first": "John", "last": "Doe", "type": NameTypeValue.LEGAL_NAME}]

        await _save_additional_names(mock_client, "test-uuid-789", names_list)

        mock_client.post.assert_not_called()

    async def test_skip_when_empty_names_list(self):
        """Test that no API call is made with empty names list."""
        mock_client = AsyncMock()

        await _save_additional_names(mock_client, "test-uuid", [])

        mock_client.post.assert_not_called()

    async def test_skip_when_names_list_is_none(self):
        """Test that no API call is made when names list is None."""
        mock_client = AsyncMock()

        await _save_additional_names(mock_client, "test-uuid", None)

        mock_client.post.assert_not_called()

    async def test_skip_additional_names_with_no_first_and_last(self):
        """Test that additional names without first and last name are skipped."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        names_list = [
            {"first": "John", "last": "Doe", "type": NameTypeValue.LEGAL_NAME},
            {"middle": "Marie", "type": NameTypeValue.MAIDEN_NAME},  # Missing first and last
            {"first": "Jane", "last": "Smith", "type": NameTypeValue.MAIDEN_NAME},
        ]

        await _save_additional_names(mock_client, "test-uuid", names_list)

        # Should only call once for Jane Smith
        assert mock_client.post.call_count == 1

    async def test_format_name_with_all_components(self):
        """Test that all name components are included in payload."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        names_list = [
            {"first": "John", "last": "Doe", "type": NameTypeValue.LEGAL_NAME},
            {
                "first": "Sarah",
                "middle": "Jane",
                "last": "Anderson",
                "suffix": "Jr.",
                "type": NameTypeValue.MAIDEN_NAME,
            },
        ]

        await _save_additional_names(mock_client, "test-uuid", names_list)

        call_args = mock_client.post.call_args
        payload = call_args[1]["json"]
        assert payload["first"] == "Sarah"
        assert payload["middle"] == "Jane"
        assert payload["last"] == "Anderson"
        assert payload["suffix"] == "Jr."
        assert payload["type"]["lookup_value_name"] == NameTypeValue.MAIDEN_NAME.value

    async def test_format_name_with_partial_components(self):
        """Test that names with missing components are handled correctly."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        names_list = [
            {"first": "John", "last": "Doe", "type": NameTypeValue.LEGAL_NAME},
            {
                "first": "Jane",
                "last": "Smith",
                "type": NameTypeValue.FORMER_NAME,
            },  # No middle or suffix
        ]

        await _save_additional_names(mock_client, "test-uuid", names_list)

        call_args = mock_client.post.call_args
        payload = call_args[1]["json"]
        assert payload["first"] == "Jane"
        assert payload["last"] == "Smith"
        assert "middle" not in payload
        assert "suffix" not in payload
        assert payload["type"]["lookup_value_name"] == NameTypeValue.FORMER_NAME.value

    async def test_handle_failed_name_creation(self):
        """Test that failed name creation is logged as warning."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=400, text="Bad Request"))

        names_list = [
            {"first": "John", "last": "Doe", "type_id": NameTypeValue.LEGAL_NAME},
            {"first": "Jane", "last": "Smith", "type_id": NameTypeValue.MAIDEN_NAME},
        ]

        with patch("intake_bot.services.legalserver.logger") as mock_logger:
            await _save_additional_names(mock_client, "test-uuid", names_list)
            mock_logger.warning.assert_called()

    async def test_handle_exception_in_save_additional_names(self):
        """Test that exceptions are handled gracefully."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Connection error"))

        names_list = [
            {"first": "John", "last": "Doe", "type_id": NameTypeValue.LEGAL_NAME},
            {"first": "Jane", "last": "Smith", "type_id": NameTypeValue.MAIDEN_NAME},
        ]

        with patch("intake_bot.services.legalserver.logger") as mock_logger:
            await _save_additional_names(mock_client, "test-uuid", names_list)
            mock_logger.error.assert_called()

    async def test_successful_name_creation_logs_debug_with_type(self):
        """Test that successful name creation is logged with type."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        names_list = [
            {"first": "John", "last": "Doe", "type": NameTypeValue.LEGAL_NAME},
            {"first": "Jane", "last": "Smith", "type": NameTypeValue.MAIDEN_NAME},
            {"first": "Bob", "last": "Johnson", "type": NameTypeValue.FORMER_NAME},
        ]

        with patch("intake_bot.services.legalserver.logger") as mock_logger:
            await _save_additional_names(mock_client, "test-uuid", names_list)
            # Should have called debug for each successful creation
            debug_calls = [
                call for call in mock_logger.debug.call_args_list if "created" in str(call).lower()
            ]
            assert len(debug_calls) >= 2


@pytest.mark.asyncio
class TestSaveAdverseParties:
    """Tests for _save_adverse_parties helper function."""

    async def test_save_single_adverse_party(self):
        """Test saving a single adverse party via API."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        adverse_parties_data = {
            "adverse_parties": [
                {"first": "Jason", "middle": "Michael", "last": "Chen", "dob": None}
            ]
        }

        await _save_adverse_parties(mock_client, "test-uuid-123", adverse_parties_data)

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "test-uuid-123/adverse_parties" in call_args[0][0]
        assert call_args[1]["json"]["first"] == "Jason"
        assert call_args[1]["json"]["middle"] == "Michael"
        assert call_args[1]["json"]["last"] == "Chen"
        assert "dob" not in call_args[1]["json"]  # None values excluded

    async def test_save_multiple_adverse_parties(self):
        """Test saving multiple adverse parties via API."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        adverse_parties_data = {
            "adverse_parties": [
                {"first": "John", "last": "Doe"},
                {"first": "Jane", "last": "Smith"},
                {"first": "Bob", "last": "Johnson"},
            ]
        }

        await _save_adverse_parties(mock_client, "test-uuid-456", adverse_parties_data)

        assert mock_client.post.call_count == 3

    async def test_adverse_party_with_dob(self):
        """Test that adverse party with DOB is included in payload."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        adverse_parties_data = {
            "adverse_parties": [{"first": "John", "last": "Doe", "dob": "1990-01-15"}]
        }

        await _save_adverse_parties(mock_client, "test-uuid", adverse_parties_data)

        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["date_of_birth"] == "1990-01-15"

    async def test_adverse_party_with_all_name_components(self):
        """Test adverse party with all name components."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        adverse_parties_data = {
            "adverse_parties": [
                {
                    "first": "Sarah",
                    "middle": "Jane",
                    "last": "Anderson",
                    "suffix": "Jr.",
                    "dob": "1985-06-20",
                }
            ]
        }

        await _save_adverse_parties(mock_client, "test-uuid", adverse_parties_data)

        call_args = mock_client.post.call_args
        payload = call_args[1]["json"]
        assert payload["first"] == "Sarah"
        assert payload["middle"] == "Jane"
        assert payload["last"] == "Anderson"
        assert payload["suffix"] == "Jr."
        assert payload["date_of_birth"] == "1985-06-20"

    async def test_skip_empty_adverse_parties_list(self):
        """Test that no API call is made when adverse parties list is empty."""
        mock_client = AsyncMock()

        adverse_parties_data = {"adverse_parties": []}

        await _save_adverse_parties(mock_client, "test-uuid", adverse_parties_data)

        mock_client.post.assert_not_called()

    async def test_skip_when_adverse_parties_data_not_dict(self):
        """Test handling of non-dict adverse parties data."""
        mock_client = AsyncMock()

        await _save_adverse_parties(mock_client, "test-uuid", "invalid-data")

        mock_client.post.assert_not_called()

    async def test_skip_adverse_party_without_first_and_last_name(self):
        """Test that adverse parties without first and last name are skipped."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        adverse_parties_data = {
            "adverse_parties": [
                {"middle": "Michael"},  # Missing first and last
                {"first": "John", "last": "Doe"},
            ]
        }

        await _save_adverse_parties(mock_client, "test-uuid", adverse_parties_data)

        # Should only call once for John Doe
        assert mock_client.post.call_count == 1

    async def test_handle_failed_adverse_party_creation(self):
        """Test that failed adverse party creation is logged as warning."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=400, text="Bad Request"))

        adverse_parties_data = {"adverse_parties": [{"first": "John", "last": "Doe"}]}

        with patch("intake_bot.services.legalserver.logger") as mock_logger:
            await _save_adverse_parties(mock_client, "test-uuid", adverse_parties_data)
            mock_logger.warning.assert_called()

    async def test_handle_exception_in_save_adverse_parties(self):
        """Test that exceptions are handled gracefully."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Connection error"))

        adverse_parties_data = {"adverse_parties": [{"first": "John", "last": "Doe"}]}

        with patch("intake_bot.services.legalserver.logger") as mock_logger:
            await _save_adverse_parties(mock_client, "test-uuid", adverse_parties_data)
            mock_logger.error.assert_called()

    async def test_successful_adverse_party_creation_logs_debug(self):
        """Test that successful adverse party creation is logged."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        adverse_parties_data = {
            "adverse_parties": [
                {"first": "John", "last": "Doe"},
                {"first": "Jane", "last": "Smith"},
            ]
        }

        with patch("intake_bot.services.legalserver.logger") as mock_logger:
            await _save_adverse_parties(mock_client, "test-uuid", adverse_parties_data)
            # Should have called debug for each successful creation
            debug_calls = [
                call for call in mock_logger.debug.call_args_list if "created" in str(call).lower()
            ]
            assert len(debug_calls) >= 2


@pytest.mark.asyncio
class TestSaveAssetsNote:
    """Tests for _save_assets_note helper function."""

    async def test_save_single_asset(self):
        """Test saving a single asset as a note."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        assets_data = {
            "listing": [{"savings account": 2100}],
            "total_value": 2100,
        }

        await _save_assets_note(mock_client, "test-uuid-123", assets_data)

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "test-uuid-123" in call_args[0][0]
        assert call_args[1]["json"]["subject"] == "Assets"
        assert "savings account: $2,100.00" in call_args[1]["json"]["body"]
        assert "Total Assets: $2,100.00" in call_args[1]["json"]["body"]
        assert call_args[1]["json"]["note_type"] == {"lookup_value_name": "General Notes"}

    async def test_save_multiple_assets(self):
        """Test saving multiple assets as a single note."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        assets_data = {
            "listing": [
                {"savings account": 2100},
                {"jewelry": 1500},
                {"vehicle": 8000},
            ],
            "total_value": 11600,
        }

        await _save_assets_note(mock_client, "test-uuid-456", assets_data)

        call_args = mock_client.post.call_args
        body = call_args[1]["json"]["body"]
        assert "savings account: $2,100.00" in body
        assert "jewelry: $1,500.00" in body
        assert "vehicle: $8,000.00" in body
        assert "Total Assets: $11,600.00" in body

    async def test_asset_formatting_with_currency(self):
        """Test that assets are formatted as currency with proper formatting."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        assets_data = {
            "listing": [{"real property": 250000}],
            "total_value": 250000,
        }

        await _save_assets_note(mock_client, "test-uuid", assets_data)

        call_args = mock_client.post.call_args
        assert "real property: $250,000.00" in call_args[1]["json"]["body"]

    async def test_skip_empty_assets_listing(self):
        """Test that no note is created when assets listing is empty and total is 0."""
        mock_client = AsyncMock()

        assets_data = {"listing": [], "total_value": 0}

        await _save_assets_note(mock_client, "test-uuid", assets_data)

        mock_client.post.assert_not_called()

    async def test_skip_when_assets_data_not_dict(self):
        """Test handling of non-dict assets data."""
        mock_client = AsyncMock()

        await _save_assets_note(mock_client, "test-uuid", "invalid-data")

        mock_client.post.assert_not_called()

    async def test_skip_assets_with_no_listing_and_zero_value(self):
        """Test that no note is created when no assets and total value is 0."""
        mock_client = AsyncMock()

        assets_data = {"listing": [], "total_value": 0}

        await _save_assets_note(mock_client, "test-uuid", assets_data)

        mock_client.post.assert_not_called()

    async def test_save_assets_with_only_total_value(self):
        """Test saving assets when only total value is present."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        assets_data = {"listing": [], "total_value": 5000}

        await _save_assets_note(mock_client, "test-uuid", assets_data)

        call_args = mock_client.post.call_args
        assert "Total Assets: $5,000.00" in call_args[1]["json"]["body"]

    async def test_handle_failed_assets_note_creation(self):
        """Test that failed note creation is logged as warning."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=400, text="Bad Request"))

        assets_data = {"listing": [{"savings": 1000}], "total_value": 1000}

        with patch("intake_bot.services.legalserver.logger") as mock_logger:
            await _save_assets_note(mock_client, "test-uuid", assets_data)
            mock_logger.warning.assert_called()

    async def test_handle_exception_in_save_assets_note(self):
        """Test that exceptions are handled gracefully."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Connection error"))

        assets_data = {"listing": [{"savings": 1000}], "total_value": 1000}

        with patch("intake_bot.services.legalserver.logger") as mock_logger:
            await _save_assets_note(mock_client, "test-uuid", assets_data)
            mock_logger.error.assert_called()

    async def test_successful_assets_note_creation_logs_debug(self):
        """Test that successful note creation is logged with total value."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        assets_data = {
            "listing": [{"savings": 1000}, {"jewelry": 500}],
            "total_value": 1500,
        }

        with patch("intake_bot.services.legalserver.logger") as mock_logger:
            await _save_assets_note(mock_client, "test-uuid", assets_data)
            call_args = mock_logger.debug.call_args_list[-1][0][0]
            assert "$1,500.00" in call_args


@pytest.mark.asyncio
class TestSaveCaseDescriptionNote:
    """Tests for _save_case_description_note helper function."""

    async def test_save_case_description(self):
        """Test saving case description as a note."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        case_type_data = {"case_description": "I need help with a divorce."}

        await _save_case_description_note(mock_client, "test-uuid-123", case_type_data)

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "test-uuid-123" in call_args[0][0]
        assert call_args[1]["json"]["subject"] == "Case Description"
        assert call_args[1]["json"]["body"] == "I need help with a divorce."
        assert call_args[1]["json"]["note_type"] == {"lookup_value_name": "General Notes"}

    async def test_skip_empty_case_description(self):
        """Test that no note is created when case description is missing."""
        mock_client = AsyncMock()

        case_type_data = {}

        await _save_case_description_note(mock_client, "test-uuid", case_type_data)

        mock_client.post.assert_not_called()


@pytest.mark.asyncio
class TestSaveIntakeLegalserver:
    """Tests for the main save_intake_legalserver function."""

    async def test_disabled_connection_returns_early(self):
        """Test that disabled connection returns without action."""
        state = {}

        with patch.dict("os.environ", {"LEGALSERVER_CONNECTION_DISABLED": "true"}):
            with patch("intake_bot.services.legalserver.logger") as mock_logger:
                from intake_bot.services.legalserver import save_intake_legalserver

                await save_intake_legalserver(state)
                mock_logger.debug.assert_called_with("LegalServer connection disabled")

    async def test_successful_matter_creation(self):
        """Test successful creation of matter in LegalServer."""
        state = {"names": {"names": [{"first": "Test", "last": "User"}]}}

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"data": {"matter_uuid": "uuid-123", "case_id": 419645}}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            with patch("intake_bot.services.legalserver.logger") as mock_logger:
                from intake_bot.services.legalserver import save_intake_legalserver

                await save_intake_legalserver(state)

                # Check that debug or info was called with matter creation message
                all_debug_calls = [call[0][0] for call in mock_logger.debug.call_args_list]
                all_info_calls = [call[0][0] for call in mock_logger.info.call_args_list]
                all_calls = all_debug_calls + all_info_calls
                assert any("Matter created successfully" in call for call in all_calls)

    async def test_failed_matter_creation(self):
        """Test handling of failed matter creation."""
        state = {"names": {"names": [{"first": "Test", "last": "User"}]}}

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.reason = "Bad Request"
        mock_response.text = "Invalid payload"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            with patch.dict("os.environ", {"LEGALSERVER_CONNECTION_DISABLED": "false"}):
                with patch("intake_bot.services.legalserver.logger") as mock_logger:
                    from intake_bot.services.legalserver import (
                        save_intake_legalserver,
                    )

                    await save_intake_legalserver(state)

                    mock_logger.error.assert_called()

    async def test_http_request_exception_handling(self):
        """Test handling of HTTP request exceptions."""
        state = {"names": {"names": [{"first": "Test", "last": "User"}]}}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection timeout"))
            mock_client_class.return_value = mock_client

            with patch.dict("os.environ", {"LEGALSERVER_CONNECTION_DISABLED": "false"}):
                with patch("intake_bot.services.legalserver.logger") as mock_logger:
                    from intake_bot.services.legalserver import (
                        save_intake_legalserver,
                    )

                    await save_intake_legalserver(state)

                    # Should log the error
                    assert any(
                        "HTTP Request failed" in str(call)
                        for call in mock_logger.error.call_args_list
                    )

    async def test_matter_creation_with_date_of_birth(self):
        """Test successful creation of matter with date of birth."""
        state = {
            "names": {"names": [{"first": "Test", "last": "User"}]},
            "date_of_birth": {"date_of_birth": "1990-05-15"},
        }

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "data": {"matter_uuid": "uuid-dob-123", "case_id": 419646}
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            with patch("intake_bot.services.legalserver.logger"):
                from intake_bot.services.legalserver import save_intake_legalserver

                await save_intake_legalserver(state)

                # Verify the matter creation call included date_of_birth
                first_call = mock_client.post.call_args_list[0]
                matter_payload = first_call[1]["json"]
                assert matter_payload["date_of_birth"] == "1990-05-15"

    async def test_matter_creation_with_different_date_formats(self):
        """Test matter creation with various valid date formats (all converted to ISO)."""
        test_dates = [
            ("1980-01-15", "1980-01-15"),  # ISO already
            ("1975-12-25", "1975-12-25"),  # ISO already
            ("1990-06-30", "1990-06-30"),  # ISO already
        ]

        for input_date, expected_iso in test_dates:
            state = {
                "names": {"names": [{"first": "Test", "last": "User"}]},
                "date_of_birth": {"date_of_birth": input_date},
            }

            mock_response = MagicMock()
            mock_response.status_code = 201
            mock_response.json.return_value = {
                "data": {"matter_uuid": f"uuid-{input_date}", "case_id": 419647}
            }

            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.__aenter__.return_value = mock_client
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client_class.return_value = mock_client

                with patch("intake_bot.services.legalserver.logger"):
                    from intake_bot.services.legalserver import save_intake_legalserver

                    await save_intake_legalserver(state)

                    # Verify the payload contains the correct ISO format date string
                    first_call = mock_client.post.call_args_list[0]
                    matter_payload = first_call[1]["json"]
                    # With mode='json', dates are serialized to ISO format strings
                    assert matter_payload["date_of_birth"] == expected_iso

    async def test_matter_creation_without_date_of_birth(self):
        """Test matter creation when date_of_birth is not provided."""
        state = {"names": {"names": [{"first": "Test", "last": "User"}]}}

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "data": {"matter_uuid": "uuid-no-dob", "case_id": 419648}
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            with patch("intake_bot.services.legalserver.logger"):
                from intake_bot.services.legalserver import save_intake_legalserver

                await save_intake_legalserver(state)

                # Verify the matter creation call does not include date_of_birth
                first_call = mock_client.post.call_args_list[0]
                matter_payload = first_call[1]["json"]
                assert "date_of_birth" not in matter_payload

    async def test_matter_creation_with_empty_date_of_birth(self):
        """Test matter creation when date_of_birth is empty string."""
        state = {
            "names": {"names": [{"first": "Test", "last": "User"}]},
            "date_of_birth": {"date_of_birth": ""},
        }

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "data": {"matter_uuid": "uuid-empty-dob", "case_id": 419649}
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            with patch("intake_bot.services.legalserver.logger"):
                from intake_bot.services.legalserver import save_intake_legalserver

                await save_intake_legalserver(state)

                # Empty date should cause validation to fail, so no POST call should be made
                # The save_intake_legalserver function returns early when payload validation fails
                assert mock_client.post.call_count == 0

    async def test_complete_intake_with_date_of_birth_and_all_fields(self):
        """Test complete matter creation with date of birth and all other required fields."""
        state = {
            "call_id": "test-call-complete-dob",
            "phone": {
                "is_valid": True,
                "phone_number": "(703) 555-1234",
                "phone_type": "mobile",
            },
            "names": {
                "names": [
                    {
                        "first": "Rebecca",
                        "middle": "Anne",
                        "last": "Thompson",
                        "suffix": None,
                    }
                ]
            },
            "date_of_birth": {"date_of_birth": "1985-07-22"},
            "service_area": {
                "location": "Richmond City",
                "is_eligible": True,
                "fips_code": 51760,
            },
            "case_type": {
                "is_eligible": True,
                "legal_problem_code": "01 Domestic Violence",
            },
            "income": {"is_eligible": True, "monthly_amount": 2500, "household_size": 2},
            "assets": {"is_eligible": True, "total_value": 5000},
            "citizenship": {"is_citizen": True},
            "domestic_violence": {
                "is_experiencing": True,
            },
        }

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "data": {"matter_uuid": "uuid-complete-dob", "case_id": 419650}
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            with patch("intake_bot.services.legalserver.logger"):
                from intake_bot.services.legalserver import save_intake_legalserver

                await save_intake_legalserver(state)

                # Verify the complete matter payload
                first_call = mock_client.post.call_args_list[0]
                matter_payload = first_call[1]["json"]

                assert matter_payload["first"] == "Rebecca"
                assert matter_payload["middle"] == "Anne"
                assert matter_payload["last"] == "Thompson"
                assert matter_payload["date_of_birth"] == "1985-07-22"
                assert matter_payload["mobile_phone"] == "(703) 555-1234"
                assert matter_payload["legal_problem_code"] == "01 Domestic Violence"
                assert matter_payload["income_eligible"] is True
                assert matter_payload["asset_eligible"] is True
                assert matter_payload["citizenship"] == "Citizen"
                assert matter_payload["victim_of_domestic_violence"] is True

    async def test_matter_creation_with_ssn_last_4(self):
        """Test successful creation of matter with SSN last 4."""
        state = {
            "names": {"names": [{"first": "Test", "last": "User"}]},
            "ssn_last_4": {"ssn_last_4": "5678"},
        }

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "data": {"matter_uuid": "uuid-ssn-123", "case_id": 419651}
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            with patch("intake_bot.services.legalserver.logger"):
                from intake_bot.services.legalserver import save_intake_legalserver

                await save_intake_legalserver(state)

                # Verify the matter creation call included ssn
                first_call = mock_client.post.call_args_list[0]
                matter_payload = first_call[1]["json"]
                assert matter_payload["ssn"] == "5678"

    async def test_matter_creation_with_case_description(self):
        """Test successful creation of matter with case description note."""
        state = {
            "names": {"names": [{"first": "Test", "last": "User"}]},
            "case_type": {
                "is_eligible": True,
                "legal_problem_code": "01 Bankruptcy",
                "case_description": "I am filing for bankruptcy.",
            },
        }

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "data": {"matter_uuid": "uuid-case-desc", "case_id": 419652}
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            with patch("intake_bot.services.legalserver.logger"):
                from intake_bot.services.legalserver import save_intake_legalserver

                await save_intake_legalserver(state)

                # Verify case description note creation
                # First call is matter creation, second should be note creation
                assert mock_client.post.call_count >= 2

                # Find the note creation call
                note_calls = [
                    call for call in mock_client.post.call_args_list if "notes" in call[0][0]
                ]
                assert len(note_calls) > 0

                note_payload = note_calls[0][1]["json"]
                assert note_payload["subject"] == "Case Description"
                assert note_payload["body"] == "I am filing for bankruptcy."

    async def test_matter_creation_with_ssn_and_date_of_birth(self):
        """Test matter creation with both SSN last 4 and date of birth."""
        state = {
            "names": {"names": [{"first": "Test", "last": "User"}]},
            "ssn_last_4": {"ssn_last_4": "9999"},
            "date_of_birth": {"date_of_birth": "1992-03-15"},
        }

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "data": {"matter_uuid": "uuid-ssn-dob-123", "case_id": 419652}
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            with patch("intake_bot.services.legalserver.logger"):
                from intake_bot.services.legalserver import save_intake_legalserver

                await save_intake_legalserver(state)

                # Verify both fields are in the payload
                first_call = mock_client.post.call_args_list[0]
                matter_payload = first_call[1]["json"]
                assert matter_payload["ssn"] == "9999"
                assert matter_payload["date_of_birth"] == "1992-03-15"

    async def test_matter_creation_complete_with_ssn(self):
        """Test successful creation of complete matter including SSN last 4."""
        state = {
            "names": {"names": [{"first": "Robert", "middle": "Lee", "last": "Garcia"}]},
            "phone": {"is_valid": True, "phone_number": "(202) 555-0199"},
            "ssn_last_4": {"ssn_last_4": "4321"},
            "date_of_birth": {"date_of_birth": "1988-11-03"},
            "service_area": {
                "location": "Alexandria City",
                "is_eligible": True,
                "fips_code": 51510,
            },
            "case_type": {
                "is_eligible": True,
                "legal_problem_code": "23 Employment",
            },
            "income": {"is_eligible": True, "monthly_amount": 3500, "household_size": 1},
            "assets": {"is_eligible": True, "total_value": 2000},
            "citizenship": {"is_citizen": True},
            "domestic_violence": {
                "is_experiencing": False,
            },
        }

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "data": {"matter_uuid": "uuid-complete-ssn", "case_id": 419653}
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            with patch("intake_bot.services.legalserver.logger"):
                from intake_bot.services.legalserver import save_intake_legalserver

                await save_intake_legalserver(state)

                # Verify the complete matter payload
                first_call = mock_client.post.call_args_list[0]
                matter_payload = first_call[1]["json"]

                assert matter_payload["first"] == "Robert"
                assert matter_payload["middle"] == "Lee"
                assert matter_payload["last"] == "Garcia"
                assert matter_payload["ssn"] == "4321"
                assert matter_payload["date_of_birth"] == "1988-11-03"
                assert matter_payload["mobile_phone"] == "(202) 555-0199"
                assert matter_payload["legal_problem_code"] == "23 Employment"
                assert matter_payload["income_eligible"] is True
                assert matter_payload["asset_eligible"] is True
                assert matter_payload["citizenship"] == "Citizen"

    async def test_matter_creation_with_household_composition(self):
        """Test successful creation of matter with household composition."""
        state = {
            "names": {"names": [{"first": "Patricia", "last": "Martinez"}]},
            "phone": {"is_valid": True, "phone_number": "(540) 555-0123"},
            "date_of_birth": {"date_of_birth": "1982-04-10"},
            "service_area": {
                "location": "Roanoke City",
                "is_eligible": True,
                "fips_code": 51740,
            },
            "case_type": {
                "is_eligible": True,
                "legal_problem_code": "31 Custody/Visitation",
            },
            "household_composition": {
                "number_of_adults": 1,
                "number_of_children": 2,
            },
            "income": {"is_eligible": True, "monthly_amount": 2800, "household_size": 3},
            "assets": {"is_eligible": True, "total_value": 1500},
            "citizenship": {"is_citizen": True},
            "domestic_violence": {
                "is_experiencing": False,
            },
        }

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "data": {"matter_uuid": "uuid-household-comp", "case_id": 419654}
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            with patch("intake_bot.services.legalserver.logger"):
                from intake_bot.services.legalserver import save_intake_legalserver

                await save_intake_legalserver(state)

                # Verify the matter payload includes household composition
                first_call = mock_client.post.call_args_list[0]
                matter_payload = first_call[1]["json"]

                assert matter_payload["first"] == "Patricia"
                assert matter_payload["last"] == "Martinez"
                assert matter_payload["number_of_adults"] == 1
                assert matter_payload["number_of_children"] == 2
                assert matter_payload["income_eligible"] is True
                assert matter_payload["asset_eligible"] is True
