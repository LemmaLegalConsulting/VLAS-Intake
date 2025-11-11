from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from intake_bot.services.legalserver import (
    _build_matter_payload,
    _parse_county_location,
    _save_income_records,
)


class TestParseCountyLocation:
    """Tests for _parse_county_location helper function."""

    def test_parse_county_with_state(self):
        """Test parsing county name with state abbreviation."""
        result = _parse_county_location("Amelia County, VA")
        assert result == {
            "county_name": "Amelia County",
            "county_state": "VA",
        }

    def test_parse_county_without_state_defaults_to_va(self):
        """Test that missing state defaults to VA."""
        result = _parse_county_location("Richmond")
        assert result == {
            "county_name": "Richmond",
            "county_state": "VA",
        }

    def test_parse_county_with_extra_whitespace(self):
        """Test that whitespace is properly trimmed."""
        result = _parse_county_location("  Fairfax County  ,  VA  ")
        assert result == {
            "county_name": "Fairfax County",
            "county_state": "VA",
        }

    def test_parse_county_with_different_state(self):
        """Test parsing with a different state."""
        result = _parse_county_location("Cook County, IL")
        assert result == {
            "county_name": "Cook County",
            "county_state": "IL",
        }

    def test_parse_county_none_returns_none(self):
        """Test that None input returns None."""
        assert _parse_county_location(None) is None

    def test_parse_county_empty_string_returns_none(self):
        """Test that empty string returns None."""
        assert _parse_county_location("") is None


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
        """Test that service area is converted to county."""
        state = {
            "names": {"names": [{"first": "Bob", "last": "Wilson"}]},
            "service_area": {"location": "Amelia County", "is_eligible": True},
        }

        payload = _build_matter_payload(state)

        assert payload["county_of_residence"] == {
            "county_name": "Amelia County",
            "county_state": "VA",
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

        assert payload["citizenship"] == "U.S. Citizen"

    def test_payload_with_citizenship_false(self):
        """Test that non-US citizenship is mapped correctly."""
        state = {
            "names": {"names": [{"first": "Frank", "last": "Garcia"}]},
            "citizenship": {"is_citizen": False},
        }

        payload = _build_matter_payload(state)

        assert payload["citizenship"] == "Non-U.S. Citizen"

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

        assert "first" not in payload
        assert "last" not in payload
        assert payload["case_disposition"] == "Incomplete Intake"

    def test_payload_with_empty_names_list(self):
        """Test payload handles empty names list gracefully."""
        state = {"names": {"names": []}}

        payload = _build_matter_payload(state)

        assert "first" not in payload
        assert "last" not in payload

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
        assert payload["county_of_residence"]["county_name"] == "Arlington County"
        assert payload["income_eligible"] is True
        assert payload["asset_eligible"] is True
        assert payload["citizenship"] == "U.S. Citizen"
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
            "listing": {"John Doe": {"wages": {"amount": 50000, "period": "year"}}},
        }

        await _save_income_records(mock_client, "test-uuid-123", income_data)

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "test-uuid-123" in call_args[0][0]
        assert call_args[1]["json"]["type"] == "Wages"
        assert call_args[1]["json"]["amount"] == "50000"
        assert call_args[1]["json"]["period"] == "Annually"

    async def test_save_multiple_income_records(self):
        """Test saving multiple income records for different household members."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        income_data = {
            "is_eligible": True,
            "listing": {
                "John Doe": {"wages": {"amount": 50000, "period": "year"}},
                "Jane Doe": {"wages": {"amount": 60000, "period": "year"}},
            },
        }

        await _save_income_records(mock_client, "test-uuid-456", income_data)

        assert mock_client.post.call_count == 2

    async def test_save_income_with_different_periods(self):
        """Test that different period formats are mapped correctly."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        income_data = {
            "listing": {
                "Person A": {"salary": {"amount": 5000, "period": "month"}},
                "Person B": {"commission": {"amount": 1000, "period": "week"}},
                "Person C": {"bonus": {"amount": 2000, "period": "biweekly"}},
            }
        }

        await _save_income_records(mock_client, "test-uuid-789", income_data)

        # Check all three calls were made with correct periods
        calls = mock_client.post.call_args_list
        assert len(calls) == 3

        # Verify period mappings
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
                "John Doe": {"wages": {"amount": 50000, "period": "year"}},
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
                    "wages": {"amount": 50000, "period": "year"},
                    "bonus": {"period": "year"},  # Missing amount
                }
            }
        }

        await _save_income_records(mock_client, "test-uuid-missing", income_data)

        # Should only call once for wages
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

        income_data = {"listing": {"John Doe": {"wages": {"amount": 50000, "period": "year"}}}}

        with patch("intake_bot.services.legalserver.logger") as mock_logger:
            await _save_income_records(mock_client, "test-uuid", income_data)
            mock_logger.warning.assert_called()

    async def test_capitalize_income_type(self):
        """Test that income types are properly capitalized."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=201))

        income_data = {
            "listing": {
                "Person": {
                    "wages": {"amount": 5000, "period": "month"},
                    "child_support": {"amount": 500, "period": "month"},
                }
            }
        }

        await _save_income_records(mock_client, "test-uuid", income_data)

        calls = mock_client.post.call_args_list
        types = [call[1]["json"]["type"] for call in calls]
        assert "Wages" in types
        assert "Child_support" in types

    async def test_handle_exception_in_save_income_records(self):
        """Test that exceptions in save_income_records are handled gracefully."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Connection error"))

        income_data = {"listing": {"John Doe": {"wages": {"amount": 50000, "period": "year"}}}}

        with patch("intake_bot.services.legalserver.logger") as mock_logger:
            # Should not raise
            await _save_income_records(mock_client, "test-uuid", income_data)
            mock_logger.error.assert_called()


@pytest.mark.asyncio
class TestSaveIntakeLegalserver:
    """Tests for the main save_intake_legalserver function."""

    async def test_disabled_connection_returns_early(self):
        """Test that disabled connection returns without action."""
        mock_flow_manager = MagicMock()
        mock_flow_manager.state = {}

        with patch(
            "intake_bot.services.legalserver.LEGALSERVER_CONNECTION_ENABLED",
            False,
        ):
            with patch("intake_bot.services.legalserver.logger") as mock_logger:
                from intake_bot.services.legalserver import save_intake_legalserver

                await save_intake_legalserver(mock_flow_manager)
                mock_logger.debug.assert_called_with("LegalServer connection disabled")

    async def test_successful_matter_creation(self):
        """Test successful creation of matter in LegalServer."""
        mock_flow_manager = MagicMock()
        mock_flow_manager.state = {"names": {"names": [{"first": "Test", "last": "User"}]}}

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"matter_uuid": "uuid-123"}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            with patch(
                "intake_bot.services.legalserver.LEGALSERVER_CONNECTION_ENABLED",
                True,
            ):
                with patch("intake_bot.services.legalserver.logger") as mock_logger:
                    from intake_bot.services.legalserver import (
                        save_intake_legalserver,
                    )

                    await save_intake_legalserver(mock_flow_manager)

                    mock_logger.info.assert_called()
                    call_args = mock_logger.info.call_args[0][0]
                    assert "uuid-123" in call_args

    async def test_failed_matter_creation(self):
        """Test handling of failed matter creation."""
        mock_flow_manager = MagicMock()
        mock_flow_manager.state = {"names": {"names": [{"first": "Test", "last": "User"}]}}

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.reason = "Bad Request"
        mock_response.text = "Invalid payload"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            with patch(
                "intake_bot.services.legalserver.LEGALSERVER_CONNECTION_ENABLED",
                True,
            ):
                with patch("intake_bot.services.legalserver.logger") as mock_logger:
                    from intake_bot.services.legalserver import (
                        save_intake_legalserver,
                    )

                    await save_intake_legalserver(mock_flow_manager)

                    mock_logger.error.assert_called()

    async def test_http_request_exception_handling(self):
        """Test handling of HTTP request exceptions."""
        mock_flow_manager = MagicMock()
        mock_flow_manager.state = {}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection timeout"))
            mock_client_class.return_value = mock_client

            with patch(
                "intake_bot.services.legalserver.LEGALSERVER_CONNECTION_ENABLED",
                True,
            ):
                with patch("intake_bot.services.legalserver.logger") as mock_logger:
                    from intake_bot.services.legalserver import (
                        save_intake_legalserver,
                    )

                    await save_intake_legalserver(mock_flow_manager)

                    mock_logger.error.assert_called()
