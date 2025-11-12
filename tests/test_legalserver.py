from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from intake_bot.services.legalserver import (
    _build_matter_payload,
    _save_additional_names_note,
    _save_adverse_parties_note,
    _save_assets_note,
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
        assert payload["case_disposition"] == "Incomplete Intake"
        assert "suffix" not in payload  # None values excluded

    def test_payload_with_phone_number(self):
        """Test that valid phone number is included."""
        state = {
            "names": {"names": [{"first": "Jane", "last": "Smith"}]},
            "phone": {"is_valid": True, "phone_number": "(866) 534-5243"},
        }

        payload = _build_matter_payload(state)

        assert payload["mobile_phone"] == "(866) 534-5243"

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

    def test_payload_with_county_of_residence(self):
        """Test that service area FIPS code is included."""
        state = {
            "names": {"names": [{"first": "Bob", "last": "Wilson"}]},
            "service_area": {"location": "Amelia County", "is_eligible": True, "fips_code": 51007},
        }

        payload = _build_matter_payload(state)

        assert payload["county_of_residence"] == {
            "county_FIPS": "51007",
        }

    def test_payload_with_income_eligibility(self):
        """Test that income eligibility flag is included."""
        state = {
            "names": {"names": [{"first": "Carol", "last": "Brown"}]},
            "income": {"is_eligible": True, "monthly_amount": 2000, "household_size": 3},
        }

        payload = _build_matter_payload(state)

        assert payload["income_eligible"] is True
        assert payload["number_of_adults"] == 3

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
            "domestic_violence": {"is_experiencing": True, "perpetrators": ["John Doe"]},
        }

        payload = _build_matter_payload(state)

        assert payload["victim_of_domestic_violence"] is True

    def test_payload_without_domestic_violence(self):
        """Test that domestic violence flag is false when not experiencing."""
        state = {
            "names": {"names": [{"first": "Henry", "last": "Zhang"}]},
            "domestic_violence": {"is_experiencing": False, "perpetrators": []},
        }

        payload = _build_matter_payload(state)

        assert payload["victim_of_domestic_violence"] is False

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
                "perpetrators": [],
            },
        }

        payload = _build_matter_payload(state)

        assert payload["first"] == "Sarah"
        assert payload["middle"] == "Jane"
        assert payload["last"] == "Anderson"
        assert payload["suffix"] == "Jr."
        assert payload["mobile_phone"] == "(703) 555-1234"
        assert payload["legal_problem_code"] == "42 Family Law/Domestic Relations"
        assert payload["county_of_residence"]["county_FIPS"] == "51013"
        assert payload["income_eligible"] is True
        assert payload["asset_eligible"] is True
        assert payload["citizenship"] == "Citizen"
        assert payload["victim_of_domestic_violence"] is False
        assert payload["case_disposition"] == "Incomplete Intake"


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
            "listing": {"John Doe": {261: {"amount": 50000, "period": "Annually"}}},
        }

        await _save_income_records(mock_client, "test-uuid-123", income_data)

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "test-uuid-123" in call_args[0][0]
        assert call_args[1]["json"]["type"] == {"lookup_value_id": 261}
        assert call_args[1]["json"]["amount"] == 50000
        assert call_args[1]["json"]["period"] == "Annually"

    async def test_save_multiple_income_records(self):
        """Test saving multiple income records for different household members."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        income_data = {
            "is_eligible": True,
            "listing": {
                "John Doe": {261: {"amount": 50000, "period": "Annually"}},
                "Jane Doe": {261: {"amount": 60000, "period": "Annually"}},
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
                "Person A": {261: {"amount": 5000, "period": "Monthly"}},
                "Person B": {268: {"amount": 1000, "period": "Weekly"}},
                "Person C": {256: {"amount": 2000, "period": "Biweekly"}},
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
                "John Doe": {261: {"amount": 50000, "period": "Annually"}},
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
                    261: {"amount": 50000, "period": "Annually"},
                    265: {"period": "Annually"},  # Missing amount
                }
            }
        }

        await _save_income_records(mock_client, "test-uuid-missing", income_data)

        # Should only call once for the income with amount
        assert mock_client.post.call_count == 1

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

        income_data = {"listing": {"John Doe": {261: {"amount": 50000, "period": "Annually"}}}}

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
                    261: {"amount": 5000, "period": "Monthly"},
                    256: {"amount": 500, "period": "Monthly"},
                }
            }
        }

        await _save_income_records(mock_client, "test-uuid", income_data)

        calls = mock_client.post.call_args_list
        types = [call[1]["json"]["type"] for call in calls]
        assert {"lookup_value_id": 261} in types
        assert {"lookup_value_id": 256} in types

    async def test_handle_exception_in_save_income_records(self):
        """Test that exceptions in save_income_records are handled gracefully."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Connection error"))

        income_data = {"listing": {"John Doe": {261: {"amount": 50000, "period": "Annually"}}}}

        with patch("intake_bot.services.legalserver.logger") as mock_logger:
            # Should not raise
            await _save_income_records(mock_client, "test-uuid", income_data)
            mock_logger.error.assert_called()


@pytest.mark.asyncio
class TestSaveAdditionalNamesNote:
    """Tests for _save_additional_names_note helper function."""

    async def test_save_single_additional_name(self):
        """Test saving a single additional name as a note."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        names_list = [
            {"first": "John", "middle": "Michael", "last": "Doe", "suffix": None},
            {"first": "Jane", "middle": "Marie", "last": "Smith", "suffix": None},
        ]

        await _save_additional_names_note(mock_client, "test-uuid-123", names_list)

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "test-uuid-123" in call_args[0][0]
        assert call_args[1]["json"]["subject"] == "Additional Names / Aliases"
        assert call_args[1]["json"]["body"] == "Jane Marie Smith"
        assert call_args[1]["json"]["note_type"] == {"lookup_value_id": 100365}

    async def test_save_multiple_additional_names(self):
        """Test saving multiple additional names, one per line."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        names_list = [
            {"first": "John", "last": "Doe"},
            {"first": "Jane", "last": "Smith"},
            {"first": "Bob", "last": "Johnson"},
            {"first": "Alice", "last": "Williams"},
        ]

        await _save_additional_names_note(mock_client, "test-uuid-456", names_list)

        call_args = mock_client.post.call_args
        expected_body = "Jane Smith\nBob Johnson\nAlice Williams"
        assert call_args[1]["json"]["body"] == expected_body

    async def test_skip_when_only_primary_name(self):
        """Test that no note is created when only primary name exists."""
        mock_client = AsyncMock()

        names_list = [{"first": "John", "last": "Doe"}]

        await _save_additional_names_note(mock_client, "test-uuid-789", names_list)

        mock_client.post.assert_not_called()

    async def test_skip_when_empty_names_list(self):
        """Test that no note is created with empty names list."""
        mock_client = AsyncMock()

        await _save_additional_names_note(mock_client, "test-uuid", [])

        mock_client.post.assert_not_called()

    async def test_skip_when_names_list_is_none(self):
        """Test that no note is created when names list is None."""
        mock_client = AsyncMock()

        await _save_additional_names_note(mock_client, "test-uuid", None)

        mock_client.post.assert_not_called()

    async def test_skip_additional_names_with_no_components(self):
        """Test that additional names with no name components are skipped."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        names_list = [
            {"first": "John", "last": "Doe"},
            {},  # Empty additional name
            {"first": "Jane", "last": "Smith"},
        ]

        await _save_additional_names_note(mock_client, "test-uuid", names_list)

        call_args = mock_client.post.call_args
        expected_body = "Jane Smith"
        assert call_args[1]["json"]["body"] == expected_body

    async def test_format_name_with_all_components(self):
        """Test that all name components (first, middle, last, suffix) are formatted correctly."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        names_list = [
            {"first": "John", "last": "Doe"},
            {
                "first": "Sarah",
                "middle": "Jane",
                "last": "Anderson",
                "suffix": "Jr.",
            },
        ]

        await _save_additional_names_note(mock_client, "test-uuid", names_list)

        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["body"] == "Sarah Jane Anderson Jr."

    async def test_format_name_with_partial_components(self):
        """Test that names with missing components are formatted correctly."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        names_list = [
            {"first": "John", "last": "Doe"},
            {"first": "Jane", "last": "Smith"},  # No middle or suffix
            {"last": "Johnson"},  # Only last name
        ]

        await _save_additional_names_note(mock_client, "test-uuid", names_list)

        call_args = mock_client.post.call_args
        expected_body = "Jane Smith\nJohnson"
        assert call_args[1]["json"]["body"] == expected_body

    async def test_handle_failed_note_creation(self):
        """Test that failed note creation is logged as warning."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=400, text="Bad Request"))

        names_list = [
            {"first": "John", "last": "Doe"},
            {"first": "Jane", "last": "Smith"},
        ]

        with patch("intake_bot.services.legalserver.logger") as mock_logger:
            await _save_additional_names_note(mock_client, "test-uuid", names_list)
            mock_logger.warning.assert_called()

    async def test_handle_exception_in_save_additional_names(self):
        """Test that exceptions are handled gracefully."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Connection error"))

        names_list = [
            {"first": "John", "last": "Doe"},
            {"first": "Jane", "last": "Smith"},
        ]

        with patch("intake_bot.services.legalserver.logger") as mock_logger:
            # Should not raise
            await _save_additional_names_note(mock_client, "test-uuid", names_list)
            mock_logger.error.assert_called()

    async def test_successful_note_creation_logs_debug(self):
        """Test that successful note creation is logged."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        names_list = [
            {"first": "John", "last": "Doe"},
            {"first": "Jane", "last": "Smith"},
            {"first": "Bob", "last": "Johnson"},
        ]

        with patch("intake_bot.services.legalserver.logger") as mock_logger:
            await _save_additional_names_note(mock_client, "test-uuid", names_list)
            # Should log success with count of names
            assert mock_logger.debug.call_count >= 1
            call_args = mock_logger.debug.call_args_list[-1][0][0]
            assert "2" in call_args  # 2 additional names (excluding primary)


@pytest.mark.asyncio
class TestSaveAdversePartiesNote:
    """Tests for _save_adverse_parties_note helper function."""

    async def test_save_single_adverse_party_note(self):
        """Test saving a single adverse party as a note."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        adverse_parties_data = {
            "adverse_parties": [
                {"first": "Jason", "middle": "Michael", "last": "Chen", "dob": None}
            ]
        }

        await _save_adverse_parties_note(mock_client, "test-uuid-123", adverse_parties_data)

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "test-uuid-123" in call_args[0][0]
        assert call_args[1]["json"]["subject"] == "Adverse Parties"
        assert call_args[1]["json"]["body"] == "Jason Michael Chen"
        assert call_args[1]["json"]["note_type"] == {"lookup_value_id": 100365}

    async def test_save_multiple_adverse_parties_note(self):
        """Test saving multiple adverse parties as a single note."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        adverse_parties_data = {
            "adverse_parties": [
                {"first": "John", "last": "Doe"},
                {"first": "Jane", "last": "Smith"},
                {"first": "Bob", "last": "Johnson"},
            ]
        }

        await _save_adverse_parties_note(mock_client, "test-uuid-456", adverse_parties_data)

        call_args = mock_client.post.call_args
        expected_body = "John Doe\nJane Smith\nBob Johnson"
        assert call_args[1]["json"]["body"] == expected_body

    async def test_adverse_party_with_dob(self):
        """Test that adverse party with DOB is formatted with DOB included."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        adverse_parties_data = {
            "adverse_parties": [{"first": "John", "last": "Doe", "dob": "1990-01-15"}]
        }

        await _save_adverse_parties_note(mock_client, "test-uuid", adverse_parties_data)

        call_args = mock_client.post.call_args
        assert "John Doe (DOB: 1990-01-15)" in call_args[1]["json"]["body"]

    async def test_adverse_party_with_all_name_components(self):
        """Test formatting adverse party with all name components."""
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

        await _save_adverse_parties_note(mock_client, "test-uuid", adverse_parties_data)

        call_args = mock_client.post.call_args
        assert "Sarah Jane Anderson Jr. (DOB: 1985-06-20)" in call_args[1]["json"]["body"]

    async def test_skip_empty_adverse_parties_list(self):
        """Test that no note is created when adverse parties list is empty."""
        mock_client = AsyncMock()

        adverse_parties_data = {"adverse_parties": []}

        await _save_adverse_parties_note(mock_client, "test-uuid", adverse_parties_data)

        mock_client.post.assert_not_called()

    async def test_skip_when_adverse_parties_data_not_dict(self):
        """Test handling of non-dict adverse parties data."""
        mock_client = AsyncMock()

        await _save_adverse_parties_note(mock_client, "test-uuid", "invalid-data")

        mock_client.post.assert_not_called()

    async def test_skip_adverse_parties_with_no_name_components(self):
        """Test that adverse parties with no name components are skipped."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        adverse_parties_data = {
            "adverse_parties": [
                {},  # Empty adverse party
                {"first": "John", "last": "Doe"},
            ]
        }

        await _save_adverse_parties_note(mock_client, "test-uuid", adverse_parties_data)

        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["body"] == "John Doe"

    async def test_handle_failed_adverse_parties_note_creation(self):
        """Test that failed note creation is logged as warning."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=400, text="Bad Request"))

        adverse_parties_data = {"adverse_parties": [{"first": "John", "last": "Doe"}]}

        with patch("intake_bot.services.legalserver.logger") as mock_logger:
            await _save_adverse_parties_note(mock_client, "test-uuid", adverse_parties_data)
            mock_logger.warning.assert_called()

    async def test_handle_exception_in_save_adverse_parties_note(self):
        """Test that exceptions are handled gracefully."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Connection error"))

        adverse_parties_data = {"adverse_parties": [{"first": "John", "last": "Doe"}]}

        with patch("intake_bot.services.legalserver.logger") as mock_logger:
            await _save_adverse_parties_note(mock_client, "test-uuid", adverse_parties_data)
            mock_logger.error.assert_called()

    async def test_successful_adverse_parties_note_creation_logs_debug(self):
        """Test that successful note creation is logged with party count."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        adverse_parties_data = {
            "adverse_parties": [
                {"first": "John", "last": "Doe"},
                {"first": "Jane", "last": "Smith"},
            ]
        }

        with patch("intake_bot.services.legalserver.logger") as mock_logger:
            await _save_adverse_parties_note(mock_client, "test-uuid", adverse_parties_data)
            call_args = mock_logger.debug.call_args_list[-1][0][0]
            assert "2" in call_args


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
        assert call_args[1]["json"]["note_type"] == {"lookup_value_id": 100365}

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
        mock_response.json.return_value = {"data": {"matter_uuid": "uuid-123"}}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            with patch.dict("os.environ", {"LEGALSERVER_CONNECTION_DISABLED": "false"}):
                with patch("intake_bot.services.legalserver.logger") as mock_logger:
                    from intake_bot.services.legalserver import save_intake_legalserver

                    await save_intake_legalserver(state)

                    mock_logger.info.assert_called()
                    call_args = mock_logger.info.call_args[0][0]
                    assert "uuid-123" in call_args

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
