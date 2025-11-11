import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from intake_bot.utils.ev import require_ev
from intake_bot.utils.globals import PROJECT_ROOT
from loguru import logger

LEGALSERVER_API_BASE_URL = (
    f"""https://{require_ev("LEGAL_SERVER_SUBDOMAIN")}.legalserver.org/api/v2/"""
)

LEGALSERVER_HEADERS = {
    "Authorization": f"""Bearer {require_ev("LEGAL_SERVER_BEARER_TOKEN")}""",
    "Content-Type": "application/json",
    "Accept": "application/json, text/html",
}

LEGALSERVER_CONNECTION_ENABLED = True


async def save_intake_legalserver(state: dict):
    """
    Save the intake state (flow_manager.state) in LegalServer.
    Extracts all collected intake data and creates a matter with related records.
    """
    if not LEGALSERVER_CONNECTION_ENABLED:
        logger.debug("LegalServer connection disabled")
        return

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Build the main matter payload
            payload = _build_matter_payload(state)
            logger.debug(f"Matter payload: {payload}")

            # Create the matter
            matter_response = await client.post(
                f"{LEGALSERVER_API_BASE_URL}matters",
                headers=LEGALSERVER_HEADERS,
                json=payload,
            )

            if matter_response.status_code not in (200, 201):
                logger.error(f"Failed to create matter: {matter_response.status_code}")
                logger.error(f"Response: {matter_response.text}")
                return

            matter_data = matter_response.json()
            logger.debug(f"Matter response data keys: {matter_data.keys()}")

            # The API returns data nested under a "data" key
            matter_info = matter_data.get("data", matter_data)
            matter_uuid = matter_info.get("matter_uuid")
            logger.info(f"Matter created successfully: {matter_uuid}")

            # Create income records if income data exists
            if "income" in state:
                # Use matter_uuid for the income endpoint
                await _save_income_records(client, matter_uuid, state["income"])

            # Create note with additional names if more than one name was provided
            if "names" in state and state["names"].get("names"):
                if len(state["names"]["names"]) > 1:
                    await _save_additional_names_note(client, matter_uuid, state["names"]["names"])

    except httpx.RequestError as e:
        logger.error(f"HTTP Request failed: {e}")
    except Exception as e:
        logger.error(f"Unexpected error saving intake to LegalServer: {e}")


def _build_matter_payload(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a LegalServer matter creation payload from state (flow_manager.state).
    """
    payload = {}

    # Required: Client name (first and last)
    if "names" in state and state["names"].get("names"):
        primary_name = state["names"]["names"][0]
        payload["first"] = primary_name.get("first")
        payload["last"] = primary_name.get("last")
        if middle := primary_name.get("middle"):
            payload["middle"] = middle
        if suffix := primary_name.get("suffix"):
            payload["suffix"] = suffix

    # Required: Case disposition
    payload["case_disposition"] = "Incomplete Intake"

    # Phone number
    if "phone" in state and isinstance(state["phone"], dict):
        if phone := state["phone"].get("phone_number"):
            payload["mobile_phone"] = phone

    # Legal problem code
    if "case_type" in state:
        if code := state["case_type"].get("legal_problem_code"):
            payload["legal_problem_code"] = code

    # Service area / County
    # All valid service areas have FIPS codes in service_areas.yaml
    if "service_area" in state:
        fips_code = state["service_area"].get("fips_code")
        if fips_code:
            payload["county_of_residence"] = {
                "county_FIPS": str(fips_code),
            }

    # Eligibility flags
    if "income" in state and isinstance(state["income"], dict):
        payload["income_eligible"] = state["income"].get("is_eligible", False)

    if "assets" in state and isinstance(state["assets"], dict):
        payload["asset_eligible"] = state["assets"].get("is_eligible", False)

    # Citizenship
    # Maps to citizenship lookup. Valid values: "Citizen" (is_citizen=True) or "Non-Citizen" (is_citizen=False)
    # See LEGALSERVER_FIELD_MAPPING.md for full mapping details.
    if "citizenship" in state and isinstance(state["citizenship"], dict):
        if "is_citizen" in state["citizenship"]:
            is_citizen = state["citizenship"].get("is_citizen")
            # Map boolean to LegalServer citizenship lookup value
            payload["citizenship"] = "Citizen" if is_citizen else "Non-Citizen"

    # Domestic violence
    if "domestic_violence" in state and isinstance(state["domestic_violence"], dict):
        if "is_experiencing" in state["domestic_violence"]:
            is_experiencing = state["domestic_violence"].get("is_experiencing")
            payload["victim_of_domestic_violence"] = is_experiencing

    # Household size
    if "income" in state and isinstance(state["income"], dict):
        if household_size := state["income"].get("household_size"):
            payload["number_of_adults"] = household_size  # Simplified; could be split

    # Exclude None values
    payload = {k: v for k, v in payload.items() if v is not None}

    return payload


async def _save_income_records(
    client: httpx.AsyncClient, matter_uuid: str, income_data: Dict[str, Any]
) -> None:
    """
    Save income records for a matter via the incomes API endpoint.

    Args:
        client: AsyncClient for making HTTP requests
        matter_uuid: The matter UUID from the matter creation response
        income_data: Dictionary with "listing" of household member income data

    Note:
        Income types from flow_manager (e.g., "wages") are mapped to LegalServer
        income categories (e.g., "Employment"). See mapping below.
    """
    if not isinstance(income_data, dict):
        logger.debug("Income data not in expected format")
        return

    listing = income_data.get("listing", {})
    if not listing:
        logger.debug("No income records to save")
        return

    try:
        # For each household member with income, create an income record
        for person_name, income_info in listing.items():
            if not income_info:  # Skip empty records
                continue

            # Extract income details (expecting format like {261: {"amount": 80000, "period": "year"}})
            # where the key is the income category ID
            for income_category_id, amount_info in income_info.items():
                if not isinstance(amount_info, dict):
                    continue

                amount = amount_info.get("amount")
                period = amount_info.get("period")

                if not amount:
                    continue

                # Map period to LegalServer values
                period_map = {
                    "year": "Annually",
                    "month": "Monthly",
                    "week": "Weekly",
                    "biweekly": "Biweekly",
                    "semimonthly": "Semi-Monthly",
                    "quarterly": "Quarterly",
                }

                # Use the income category ID directly as the LegalServer lookup value
                payload = {
                    "type": {"lookup_value_id": income_category_id},
                    "amount": amount,  # Send as number, not string
                    "period": period_map.get(period, "Monthly"),
                }

                response = await client.post(
                    f"{LEGALSERVER_API_BASE_URL}matters/{matter_uuid}/incomes",
                    headers=LEGALSERVER_HEADERS,
                    json=payload,
                )

                if response.status_code not in (200, 201):
                    logger.warning(
                        f"Failed to save income for {person_name} (ID: {income_category_id}): "
                        f"{response.status_code} - {response.text}"
                    )
                else:
                    logger.debug(
                        f"Income record created for {person_name} (ID: {income_category_id}): {amount} {period}"
                    )

    except Exception as e:
        logger.error(f"Error saving income records: {e}")


async def _save_additional_names_note(
    client: httpx.AsyncClient, matter_uuid: str, names_list: list[Dict[str, Any]]
) -> None:
    """
    Save additional caller names as a matter note.

    Args:
        client: AsyncClient for making HTTP requests
        matter_uuid: The matter UUID from the matter creation response
        names_list: List of name dictionaries with first, middle, last, suffix fields
    """
    if not names_list or len(names_list) <= 1:
        logger.debug("No additional names to save")
        return

    try:
        # Skip the primary name (index 0) and format additional names
        additional_names = []
        for name in names_list[1:]:
            # Build full name from components
            name_parts = []
            if first := name.get("first"):
                name_parts.append(first)
            if middle := name.get("middle"):
                name_parts.append(middle)
            if last := name.get("last"):
                name_parts.append(last)
            if suffix := name.get("suffix"):
                name_parts.append(suffix)

            if name_parts:
                additional_names.append(" ".join(name_parts))

        if not additional_names:
            logger.debug("No additional names to format")
            return

        # Create note with one name per line
        note_body = "\n".join(additional_names)

        payload = {
            "subject": "Additional Names / Aliases",
            "body": note_body,
            "note_type": {"lookup_value_name": "Intake"},
        }

        response = await client.post(
            f"{LEGALSERVER_API_BASE_URL}matters/{matter_uuid}/notes",
            headers=LEGALSERVER_HEADERS,
            json=payload,
        )

        if response.status_code not in (200, 201):
            logger.warning(
                f"Failed to save additional names note: {response.status_code} - {response.text}"
            )
        else:
            logger.debug(f"Additional names note created with {len(additional_names)} name(s)")

    except Exception as e:
        logger.error(f"Error saving additional names note: {e}")


def load_state_by_call_id(call_id: str) -> Optional[Dict[str, Any]]:
    """
    Load intake state from flow_manager_state.json by call_id.

    Args:
        call_id: The call ID to look up

    Returns:
        The state dictionary for the call, or None if not found
    """
    state_file = Path(PROJECT_ROOT) / "logs/flow_manager_state.json"

    if not state_file.exists():
        logger.error(f"State file not found: {state_file}")
        return None

    try:
        with open(state_file, "r") as f:
            all_states = json.load(f)

        if call_id not in all_states:
            logger.error(f"No state found for call_id: {call_id}")
            return None

        state = all_states[call_id]
        logger.info(f"Loaded state for call_id: {call_id}")
        return state

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse state file: {e}")
        return None
    except Exception as e:
        logger.error(f"Error loading state: {e}")
        return None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python legalserver.py <call_id>")
        sys.exit(1)

    call_id = sys.argv[1]
    state = load_state_by_call_id(call_id)

    if state is None:
        print(f"Failed to load state for call_id: {call_id}")
        sys.exit(1)

    print(f"Loaded state for call_id: {call_id}")
    print(json.dumps(state, indent=2))

    asyncio.run(save_intake_legalserver(state))
