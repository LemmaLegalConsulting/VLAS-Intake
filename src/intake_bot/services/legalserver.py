import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from intake_bot.utils.ev import ev_is_true, require_ev
from intake_bot.utils.globals import PROJECT_ROOT
from loguru import logger

LEGALSERVER_API_BASE_URL = (
    f"""https://{require_ev("LEGAL_SERVER_SUBDOMAIN")}.legalserver.org/api/v2"""
)

LEGALSERVER_HEADERS = {
    "Authorization": f"""Bearer {require_ev("LEGAL_SERVER_BEARER_TOKEN")}""",
    "Content-Type": "application/json",
    "Accept": "application/json, text/html",
}


async def save_intake_legalserver(state: dict):
    """
    Save the intake state (flow_manager.state) in LegalServer.
    Extracts all collected intake data and creates a matter with related records.
    """
    if ev_is_true("LEGALSERVER_CONNECTION_DISABLED"):
        logger.debug("LegalServer connection disabled")
        return

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            payload = _build_matter_payload(state)
            if payload is None:
                logger.warning("Skipping LegalServer save: required fields missing")
                return
            logger.debug(f"Matter payload: {payload}")

            matter_response = await client.post(
                f"{LEGALSERVER_API_BASE_URL}/matters",
                headers=LEGALSERVER_HEADERS,
                json=payload,
            )

            if matter_response.status_code not in (200, 201):
                logger.error(f"Failed to create matter: {matter_response.status_code}")
                logger.error(f"Response: {matter_response.text}")
                return

            matter_data = matter_response.json()
            logger.debug(f"Matter response data keys: {matter_data.keys()}")

            matter_info = matter_data.get("data", matter_data)
            matter_uuid = matter_info.get("matter_uuid")

            if case_id := matter_info.get("case_id"):
                subdomain = require_ev("LEGAL_SERVER_SUBDOMAIN")
                profile_url = f"https://{subdomain}.legalserver.org/matter/profile/view/{case_id}"
                logger.debug(f"Matter created successfully: {matter_uuid} - View: {profile_url}")
            else:
                logger.debug(f"Matter created successfully: {matter_uuid}")

            if "income" in state:
                await _save_income_records(client, matter_uuid, state["income"])

            if "adverse_parties" in state:
                await _save_adverse_parties(client, matter_uuid, state["adverse_parties"])

            if "assets" in state:
                await _save_assets_note(client, matter_uuid, state["assets"])

            if "names" in state and "names" in state["names"]:
                if len(state["names"]["names"]) > 1:
                    await _save_additional_names(client, matter_uuid, state["names"]["names"])

    except httpx.RequestError as e:
        logger.error(f"HTTP Request failed: {e}")
    except Exception as e:
        logger.error(f"Unexpected error saving intake to LegalServer: {e}")


def _build_matter_payload(state: Dict[str, Any]) -> Dict[str, Any] | None:
    """
    Build a LegalServer matter creation payload from state (flow_manager.state).

    Returns None if required fields (first and last names) are missing.
    """
    if not ("names" in state and state["names"].get("names")):
        logger.warning("Cannot create matter: names not found in state")
        return None

    primary_name = state["names"]["names"][0]
    first = primary_name.get("first")
    last = primary_name.get("last")

    if not first or not last:
        logger.warning(
            f"Cannot create matter: missing first or last name (first={first}, last={last})"
        )
        return None

    payload = {}
    payload["first"] = first
    payload["last"] = last
    if middle := primary_name.get("middle"):
        payload["middle"] = middle
    if suffix := primary_name.get("suffix"):
        payload["suffix"] = suffix

    # Required: Case disposition
    payload["case_disposition"] = "Incomplete Intake"

    if "phone" in state and isinstance(state["phone"], dict):
        if phone := state["phone"].get("phone_number"):
            payload["mobile_phone"] = phone

    if "case_type" in state:
        if code := state["case_type"].get("legal_problem_code"):
            payload["legal_problem_code"] = code

    if "service_area" in state:
        fips_code = state["service_area"].get("fips_code")
        if fips_code:
            payload["county_of_residence"] = {
                "county_FIPS": str(fips_code),
            }

    if "income" in state and isinstance(state["income"], dict):
        payload["income_eligible"] = state["income"].get("is_eligible", False)

    if "assets" in state and isinstance(state["assets"], dict):
        payload["asset_eligible"] = state["assets"].get("is_eligible", False)

    # Maps to citizenship lookup. Valid values: "Citizen" (is_citizen=True) or "Non-Citizen" (is_citizen=False)
    if "citizenship" in state and isinstance(state["citizenship"], dict):
        if "is_citizen" in state["citizenship"]:
            is_citizen = state["citizenship"].get("is_citizen")
            # Map boolean to LegalServer citizenship lookup value
            payload["citizenship"] = "Citizen" if is_citizen else "Non-Citizen"

    if "domestic_violence" in state and isinstance(state["domestic_violence"], dict):
        if "is_experiencing" in state["domestic_violence"]:
            is_experiencing = state["domestic_violence"].get("is_experiencing")
            payload["victim_of_domestic_violence"] = is_experiencing

    if "income" in state and isinstance(state["income"], dict):
        if household_size := state["income"].get("household_size"):
            payload["number_of_adults"] = household_size

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
    """
    if not isinstance(income_data, dict):
        logger.debug("Income data not in expected format")
        return

    listing = income_data.get("listing", {})
    if not listing:
        logger.debug("No income records to save")
        return

    try:
        for person_name, income_info in listing.items():
            if not income_info:
                continue

            for income_category_id, amount_info in income_info.items():
                if not isinstance(amount_info, dict):
                    continue

                amount = amount_info.get("amount")
                period = amount_info.get("period")
                if amount is None or period is None:
                    continue

                payload = {
                    "type": {"lookup_value_id": income_category_id},
                    "amount": amount,
                    "period": period,
                }

                response = await client.post(
                    f"{LEGALSERVER_API_BASE_URL}/matters/{matter_uuid}/incomes",
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


async def _save_additional_names(
    client: httpx.AsyncClient, matter_uuid: str, names_list: list[Dict[str, Any]]
) -> None:
    """
    Save additional caller names via the additional_names API endpoint.

    Args:
        client: AsyncClient for making HTTP requests
        matter_uuid: The matter UUID from the matter creation response
        names_list: List of name dictionaries with first, middle, last, suffix fields
    """
    if not names_list or len(names_list) <= 1:
        logger.debug("No additional names to save")
        return

    try:
        # Skip the primary name (index 0) and save each additional name
        for name in names_list[1:]:
            payload = {}

            if first := name.get("first"):
                payload["first"] = first
            if last := name.get("last"):
                payload["last"] = last
            if middle := name.get("middle"):
                payload["middle"] = middle
            if suffix := name.get("suffix"):
                payload["suffix"] = suffix

            if not (payload.get("first") and payload.get("last")):
                logger.debug("Skipping additional name: missing first or last name")
                continue

            # Type is required - get type_id from name object (should be int from model dump)
            # Default to 333 (Former Name) if somehow not set
            type_id = name.get("type_id", 333)
            payload["type"] = {"lookup_value_id": type_id}

            response = await client.post(
                f"{LEGALSERVER_API_BASE_URL}/matters/{matter_uuid}/additional_names",
                headers=LEGALSERVER_HEADERS,
                json=payload,
            )

            if response.status_code not in (200, 201):
                logger.warning(
                    f"Failed to save additional name {payload.get('first')} {payload.get('last')}: "
                    f"{response.status_code} - {response.text}"
                )
            else:
                logger.debug(
                    f"Additional name created: {payload.get('first')} {payload.get('last')} (type_id: {type_id})"
                )

    except Exception as e:
        logger.error(f"Error saving additional names: {e}")


async def _save_adverse_parties(
    client: httpx.AsyncClient, matter_uuid: str, adverse_parties_data: Dict[str, Any]
) -> None:
    """
    Save adverse parties for a matter via the adverse_parties API endpoint.

    Args:
        client: AsyncClient for making HTTP requests
        matter_uuid: The matter UUID from the matter creation response
        adverse_parties_data: Dictionary with "adverse_parties" list of party data
    """
    if not isinstance(adverse_parties_data, dict):
        logger.debug("Adverse parties data not in expected format")
        return

    parties = adverse_parties_data.get("adverse_parties", [])
    if not parties:
        logger.debug("No adverse parties to save")
        return

    try:
        for party in parties:
            payload = {}

            if first := party.get("first"):
                payload["first"] = first
            if last := party.get("last"):
                payload["last"] = last
            if middle := party.get("middle"):
                payload["middle"] = middle
            if suffix := party.get("suffix"):
                payload["suffix"] = suffix
            if dob := party.get("dob"):
                payload["date_of_birth"] = dob

            if not (payload.get("first") and payload.get("last")):
                logger.debug("Skipping adverse party: missing first or last name")
                continue

            response = await client.post(
                f"{LEGALSERVER_API_BASE_URL}/matters/{matter_uuid}/adverse_parties",
                headers=LEGALSERVER_HEADERS,
                json=payload,
            )

            if response.status_code not in (200, 201):
                logger.warning(
                    f"Failed to save adverse party {payload.get('first')} {payload.get('last')}: "
                    f"{response.status_code} - {response.text}"
                )
            else:
                logger.debug(f"Adverse party created: {payload.get('first')} {payload.get('last')}")

    except Exception as e:
        logger.error(f"Error saving adverse parties: {e}")


async def _save_assets_note(
    client: httpx.AsyncClient, matter_uuid: str, assets_data: Dict[str, Any]
) -> None:
    """
    Save assets as a matter note.

    Args:
        client: AsyncClient for making HTTP requests
        matter_uuid: The matter UUID from the matter creation response
        assets_data: Dictionary with "listing" and "total_value" of assets
    """
    if not isinstance(assets_data, dict):
        logger.debug("Assets data not in expected format")
        return

    listing = assets_data.get("listing", [])
    total_value = assets_data.get("total_value", 0)

    if not listing and total_value == 0:
        logger.debug("No assets to save")
        return

    try:
        asset_lines = []

        if listing:
            for asset_dict in listing:
                if isinstance(asset_dict, dict):
                    for asset_type, amount in asset_dict.items():
                        asset_lines.append(f"{asset_type}: ${amount:,.2f}")

        if total_value > 0:
            asset_lines.append(f"\nTotal Assets: ${total_value:,.2f}")

        if not asset_lines:
            logger.debug("No assets to format")
            return

        note_body = "\n".join(asset_lines)

        # Use "General Notes" (ID: 100365) as the note type for assets
        payload = {
            "subject": "Assets",
            "body": note_body,
            "note_type": {"lookup_value_id": 100365},
        }

        response = await client.post(
            f"{LEGALSERVER_API_BASE_URL}/matters/{matter_uuid}/notes",
            headers=LEGALSERVER_HEADERS,
            json=payload,
        )

        if response.status_code not in (200, 201):
            logger.warning(f"Failed to save assets note: {response.status_code} - {response.text}")
        else:
            logger.debug(f"Assets note created with total value: ${total_value:,.2f}")

    except Exception as e:
        logger.error(f"Error saving assets note: {e}")


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
