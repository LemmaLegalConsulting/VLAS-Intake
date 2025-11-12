import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from intake_bot.models.legalserver import (
    AdditionalNamePayload,
    AdversePartyPayload,
    IncomePayload,
    LegalServerCreateMatterPayload,
    NotePayload,
)
from intake_bot.utils.ev import ev_is_true, require_ev
from intake_bot.utils.globals import PROJECT_ROOT
from loguru import logger
from pydantic import ValidationError

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

    Validates the payload against LegalServerCreateMatterPayload model.
    Returns None if required fields are missing or validation fails.
    """
    names_list = state.get("names", {}).get("names", [])
    if not names_list:
        logger.warning("Cannot create matter: names not found or empty in state")
        return None

    primary_name = names_list[0]

    payload = {**primary_name}

    if isinstance(state.get("phone"), dict):
        phone_number = state["phone"].get("phone_number")
        phone_type = state["phone"].get("phone_type", "mobile")
        if phone_number and phone_type:
            payload[f"{phone_type}_phone"] = phone_number

    if isinstance(state.get("case_type"), dict):
        payload["legal_problem_code"] = state["case_type"].get("legal_problem_code")

    if isinstance(state.get("service_area"), dict):
        if fips_code := state["service_area"].get("fips_code"):
            payload["county_of_residence"] = {"county_FIPS": str(fips_code)}

    if isinstance(state.get("income"), dict):
        payload["income_eligible"] = state["income"].get("is_eligible")
        payload["number_of_adults"] = state["income"].get("household_size")

    if isinstance(state.get("assets"), dict):
        payload["asset_eligible"] = state["assets"].get("is_eligible")

    if isinstance(state.get("citizenship"), dict):
        payload["citizenship"] = state["citizenship"].get("is_citizen")

    if isinstance(state.get("domestic_violence"), dict):
        payload["victim_of_domestic_violence"] = state["domestic_violence"].get("is_experiencing")

    try:
        validated = LegalServerCreateMatterPayload(**payload)
        return validated.model_dump(exclude_none=True)
    except ValidationError as e:
        logger.warning(f"Cannot create matter: validation failed - {e}")
        return None


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
            for income_category_id, amount_info in (income_info or {}).items():
                try:
                    payload = IncomePayload(
                        type={"lookup_value_id": income_category_id},
                        amount=amount_info.get("amount"),
                        period=amount_info.get("period"),
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to validate income for {person_name} (ID: {income_category_id}): {e}"
                    )
                    continue

                response = await client.post(
                    f"{LEGALSERVER_API_BASE_URL}/matters/{matter_uuid}/incomes",
                    headers=LEGALSERVER_HEADERS,
                    json=payload.model_dump(exclude_none=True),
                )

                if response.status_code not in (200, 201):
                    logger.warning(
                        f"Failed to save income for {person_name} (ID: {income_category_id}): "
                        f"{response.status_code} - {response.text}"
                    )
                else:
                    logger.debug(
                        f"Income record created for {person_name} (ID: {income_category_id}): {payload.amount} {payload.period}"
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
            try:
                payload = AdditionalNamePayload(
                    first=name.get("first"),
                    last=name.get("last"),
                    middle=name.get("middle"),
                    suffix=name.get("suffix"),
                    type={"lookup_value_id": name.get("type_id", 333)},
                )
            except Exception as e:
                logger.warning(f"Failed to validate additional name: {e}")
                continue

            response = await client.post(
                f"{LEGALSERVER_API_BASE_URL}/matters/{matter_uuid}/additional_names",
                headers=LEGALSERVER_HEADERS,
                json=payload.model_dump(exclude_none=True),
            )

            if response.status_code not in (200, 201):
                logger.warning(
                    f"Failed to save additional name {payload.first} {payload.last}: "
                    f"{response.status_code} - {response.text}"
                )
            else:
                logger.debug(f"Additional name created: {payload.first} {payload.last}")

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
            try:
                # Extract base party data
                payload_data = {
                    "first": party.get("first"),
                    "last": party.get("last"),
                    "middle": party.get("middle"),
                    "suffix": party.get("suffix"),
                    "date_of_birth": party.get("dob"),
                }

                # Add phones if present - map from phones array to individual phone fields
                phones = party.get("phones", [])
                if phones:
                    for phone in phones:
                        phone_number = phone.get("number")
                        phone_type = phone.get("type", "").lower()
                        if phone_number and phone_type:
                            # Map phone type to field name: phone_{type}
                            field_name = f"phone_{phone_type}"
                            payload_data[field_name] = phone_number

                payload = AdversePartyPayload(**payload_data)
            except Exception as e:
                logger.warning(f"Failed to validate adverse party: {e}")
                continue

            response = await client.post(
                f"{LEGALSERVER_API_BASE_URL}/matters/{matter_uuid}/adverse_parties",
                headers=LEGALSERVER_HEADERS,
                json=payload.model_dump(exclude_none=True),
            )

            if response.status_code not in (200, 201):
                logger.warning(
                    f"Failed to save adverse party {payload.first} {payload.last}: "
                    f"{response.status_code} - {response.text}"
                )
            else:
                logger.debug(f"Adverse party created: {payload.first} {payload.last}")

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

        try:
            payload = NotePayload(
                subject="Assets",
                body="\n".join(asset_lines),
                note_type={"lookup_value_id": 100365},
            )
        except Exception as e:
            logger.warning(f"Failed to validate assets note: {e}")
            return

        response = await client.post(
            f"{LEGALSERVER_API_BASE_URL}/matters/{matter_uuid}/notes",
            headers=LEGALSERVER_HEADERS,
            json=payload.model_dump(exclude_none=True),
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
