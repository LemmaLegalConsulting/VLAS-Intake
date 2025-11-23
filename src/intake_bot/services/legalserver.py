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
            logger.debug(f"""Matter payload: {payload}""")

            matter_response = await client.post(
                f"""{LEGALSERVER_API_BASE_URL}/matters""",
                headers=LEGALSERVER_HEADERS,
                json=payload,
            )

            if matter_response.status_code not in (200, 201):
                logger.error(f"""Failed to create matter: {matter_response.status_code}""")
                logger.error(f"""Response: {matter_response.text}""")
                return

            matter_data = matter_response.json()
            logger.debug(f"""Matter response data keys: {matter_data.keys()}""")

            matter_info = matter_data.get("data", matter_data)
            matter_uuid = matter_info.get("matter_uuid")

            if case_id := matter_info.get("case_id"):
                subdomain = require_ev("LEGAL_SERVER_SUBDOMAIN")
                profile_url = (
                    f"""https://{subdomain}.legalserver.org/matter/profile/view/{case_id}"""
                )
                logger.debug(
                    f"""Matter created successfully: {matter_uuid} - View: {profile_url}"""
                )
            else:
                logger.debug(f"""Matter created successfully: {matter_uuid}""")

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
        logger.error(f"""HTTP Request failed: {e}""")
    except Exception as e:
        logger.error(f"""Unexpected error saving intake to LegalServer: {e}""")


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
            payload[f"""{phone_type}_phone"""] = phone_number

    if isinstance(state.get("case_type"), dict):
        payload["legal_problem_code"] = state["case_type"].get("legal_problem_code")

    if isinstance(state.get("service_area"), dict):
        if fips_code := state["service_area"].get("fips_code"):
            payload["county_of_residence"] = {"county_FIPS": str(fips_code)}

    if isinstance(state.get("income"), dict):
        payload["income_eligible"] = state["income"].get("is_eligible")
        payload["number_of_adults"] = state["income"].get("household_size")

    if isinstance(state.get("household_composition"), dict):
        payload["number_of_adults"] = state["household_composition"].get("number_of_adults")
        payload["number_of_children"] = state["household_composition"].get("number_of_children")

    if isinstance(state.get("assets"), dict):
        payload["asset_eligible"] = state["assets"].get("is_eligible")

    if isinstance(state.get("citizenship"), dict):
        payload["citizenship"] = state["citizenship"].get("is_citizen")

    if isinstance(state.get("ssn_last_4"), dict):
        payload["ssn"] = state["ssn_last_4"].get("ssn_last_4")

    if isinstance(state.get("date_of_birth"), dict):
        payload["date_of_birth"] = state["date_of_birth"].get("date_of_birth")

    if isinstance(state.get("address"), dict):
        payload["home_street"] = state["address"]["address"].get("street")
        payload["home_apt_num"] = state["address"]["address"].get("street_2")
        payload["home_city"] = state["address"]["address"].get("city")
        payload["home_state"] = state["address"]["address"].get("state")
        payload["home_zip"] = state["address"]["address"].get("zip")

    if isinstance(state.get("domestic_violence"), dict):
        payload["victim_of_domestic_violence"] = state["domestic_violence"].get("is_experiencing")

    try:
        validated = LegalServerCreateMatterPayload(**payload)
        return validated.model_dump(mode="json", exclude_none=True)
    except ValidationError as e:
        logger.warning(f"""Cannot create matter: validation failed - {e}""")
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
            Structure: {person_name: {income_category_name: {amount, period}}}
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
            for income_category_name, amount_info in (income_info or {}).items():
                try:
                    payload = IncomePayload(
                        type={"lookup_value_name": income_category_name},
                        amount=amount_info.get("amount"),
                        period=amount_info.get("period"),
                    )
                except Exception as e:
                    logger.warning(
                        f"""Failed to validate income for {person_name} ({income_category_name}): {e}"""
                    )
                    continue

                response = await client.post(
                    f"""{LEGALSERVER_API_BASE_URL}/matters/{matter_uuid}/incomes""",
                    headers=LEGALSERVER_HEADERS,
                    json=payload.model_dump(exclude_none=True),
                )

                if response.status_code not in (200, 201):
                    logger.warning(
                        f"""Failed to save income for {person_name} ({income_category_name}): """
                        f"""{response.status_code} - {response.text}"""
                    )
                else:
                    logger.debug(
                        f"""Income record created for {person_name} ({income_category_name}): ${payload.amount} {payload.period}"""
                    )

    except Exception as e:
        logger.error(f"""Error saving income records: {e}""")


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
                    type={"lookup_value_name": name.get("type", "Former Name")},
                )
            except Exception as e:
                logger.warning(f"""Failed to validate additional name: {e}""")
                continue

            response = await client.post(
                f"""{LEGALSERVER_API_BASE_URL}/matters/{matter_uuid}/additional_names""",
                headers=LEGALSERVER_HEADERS,
                json=payload.model_dump(exclude_none=True),
            )

            if response.status_code not in (200, 201):
                logger.warning(
                    f"""Failed to save additional name {payload.first} {payload.last}: """
                    f"""{response.status_code} - {response.text}"""
                )
            else:
                logger.debug(f"""Additional name created: {payload.first} {payload.last}""")

    except Exception as e:
        logger.error(f"""Error saving additional names: {e}""")


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
                            field_name = f"""phone_{phone_type}"""
                            payload_data[field_name] = phone_number

                payload = AdversePartyPayload(**payload_data)
            except Exception as e:
                logger.warning(f"""Failed to validate adverse party: {e}""")
                continue

            response = await client.post(
                f"""{LEGALSERVER_API_BASE_URL}/matters/{matter_uuid}/adverse_parties""",
                headers=LEGALSERVER_HEADERS,
                json=payload.model_dump(exclude_none=True),
            )

            if response.status_code not in (200, 201):
                logger.warning(
                    f"""Failed to save adverse party {payload.first} {payload.last}: """
                    f"""{response.status_code} - {response.text}"""
                )
            else:
                logger.debug(f"""Adverse party created: {payload.first} {payload.last}""")

    except Exception as e:
        logger.error(f"""Error saving adverse parties: {e}""")


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
                        asset_lines.append(f"""{asset_type}: ${amount:,.2f}""")

        if total_value > 0:
            asset_lines.append(f"""\nTotal Assets: ${total_value:,.2f}""")

        if not asset_lines:
            logger.debug("No assets to format")
            return

        try:
            payload = NotePayload(
                subject="Assets",
                body="\n".join(asset_lines),
                note_type={"lookup_value_name": "General Notes"},
            )
        except Exception as e:
            logger.warning(f"""Failed to validate assets note: {e}""")
            return

        response = await client.post(
            f"""{LEGALSERVER_API_BASE_URL}/matters/{matter_uuid}/notes""",
            headers=LEGALSERVER_HEADERS,
            json=payload.model_dump(exclude_none=True),
        )

        if response.status_code not in (200, 201):
            logger.warning(
                f"""Failed to save assets note: {response.status_code} - {response.text}"""
            )
        else:
            logger.debug(f"""Assets note created with total value: ${total_value:,.2f}""")

    except Exception as e:
        logger.error(f"""Error saving assets note: {e}""")


async def get_common_lookup_types() -> list[str] | None:
    """
    Get list of common system lookup types available in LegalServer.

    LegalServer API v2 does not provide an endpoint to list all lookups,
    so this returns a curated list of common system lookups that can be queried.

    You can query any lookup type with query_lookup_values(), even if it's not
    in this common list, as long as it exists in your LegalServer instance.

    Returns:
        List of common lookup type table names
    """
    common_lookup_types = [
        "alias_type",
        "citizenship",
        "country_of_origin",
        "current_living_situation",
        "employment_status",
        "ethnicity",
        "how_referred",
        "immigration_status",
        "income_type",
        "language",
        "legal_problem_category",
        "marital_status",
        "military_service",
        "military_status",
        "note_type",
        "race",
    ]
    return common_lookup_types


async def get_custom_lookups() -> Dict[str, Any] | None:
    """
    Query LegalServer API for all custom lookup tables with pagination support.

    Custom lookups are user-defined lookup tables specific to each LegalServer instance.
    Fetches all pages and combines results into a single list.

    Returns:
        Dictionary with custom lookups data including all pages, or None if query fails
    """
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            all_lookups = []
            page_number = 1
            total_pages = None

            while total_pages is None or page_number <= total_pages:
                # Query custom lookups endpoint with pagination
                response = await client.get(
                    f"{LEGALSERVER_API_BASE_URL}/custom_lookups?page_number={page_number}",
                    headers=LEGALSERVER_HEADERS,
                )

                if response.status_code not in (200, 201):
                    logger.error(
                        f"Failed to query custom lookups page {page_number}: {response.status_code}"
                    )
                    logger.error(f"Response: {response.text}")
                    return None

                data = response.json()
                logger.debug(f"Custom lookups page {page_number} response keys: {data.keys()}")

                # Extract pagination info
                if total_pages is None:
                    total_pages = data.get("total_number_of_pages", 1)
                    logger.debug(
                        f"Total pages: {total_pages}, Total records: {data.get('total_records', 0)}"
                    )

                # Combine data from all pages
                page_data = data.get("data", [])
                all_lookups.extend(page_data)
                logger.debug(
                    f"Page {page_number}: retrieved {len(page_data)} lookups (total so far: {len(all_lookups)})"
                )

                page_number += 1

            # Return combined result with all lookups
            return {
                "total_records": data.get("total_records", len(all_lookups)),
                "total_pages": total_pages,
                "data": all_lookups,
            }

    except httpx.RequestError as e:
        logger.error(f"HTTP Request failed: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error querying custom lookups: {e}")
        return None


async def query_lookup_values(
    lookup_identifier: str, is_custom: bool = False
) -> Dict[str, Any] | None:
    """
    Query LegalServer API for lookup values of a specific type.

    Args:
        lookup_identifier: For system lookups: the lookup table name (e.g., "alias_type", "income_type")
                          For custom lookups: the lookup table UUID
        is_custom: If True, query custom lookups; if False, query system lookups (default)

    Returns:
        Dictionary of lookup values with their IDs and names, or None if query fails
    """
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Construct URL based on lookup type
            if is_custom:
                url = f"{LEGALSERVER_API_BASE_URL}/custom_lookups/{lookup_identifier}"
            else:
                url = f"{LEGALSERVER_API_BASE_URL}/lookups/{lookup_identifier}"

            response = await client.get(url, headers=LEGALSERVER_HEADERS)

            if response.status_code not in (200, 201):
                lookup_type = "custom lookup" if is_custom else "lookup table"
                logger.error(
                    f"Failed to query {lookup_type} '{lookup_identifier}': {response.status_code}"
                )
                logger.error(f"Response: {response.text}")
                return None

            data = response.json()
            logger.debug(f"Lookup response keys: {data.keys()}")

            # Extract lookup values from response
            lookup_values = data.get("data", data)
            if isinstance(lookup_values, dict) and "data" in data:
                # Nested response
                values = lookup_values
            elif isinstance(lookup_values, list):
                # List of values
                values = lookup_values
            else:
                values = lookup_values

            return {
                "lookup_type": lookup_identifier,
                "is_custom": is_custom,
                "values": values,
                "raw_response": data,
            }

    except httpx.RequestError as e:
        logger.error(f"HTTP Request failed: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error querying lookup values: {e}")
        return None


async def find_lookup_by_id(lookup_value_id: int) -> Dict[str, Any] | None:
    """
    Search for a lookup value by ID across all queryable system lookup types.

    This can help identify which lookup table contains a specific ID.

    Args:
        lookup_value_id: The ID to search for

    Returns:
        Dictionary with lookup_type and the matching lookup value, or None if not found
    """
    # Get all common lookup types
    common_types = await get_common_lookup_types()
    if not common_types:
        logger.error("Failed to get common lookup types")
        return None

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Query each lookup type to find the ID
            for lookup_type in common_types:
                url = f"{LEGALSERVER_API_BASE_URL}/lookups/{lookup_type}"

                try:
                    response = await client.get(url, headers=LEGALSERVER_HEADERS, timeout=10)

                    if response.status_code not in (200, 201):
                        continue

                    data = response.json()
                    values = data.get("data", [])

                    # Handle both list and dict responses
                    if isinstance(values, list):
                        for item in values:
                            if isinstance(item, dict) and item.get("id") == lookup_value_id:
                                return {
                                    "lookup_type": lookup_type,
                                    "lookup_value": item,
                                }
                    elif isinstance(values, dict):
                        if values.get("id") == lookup_value_id:
                            return {
                                "lookup_type": lookup_type,
                                "lookup_value": values,
                            }
                except Exception as e:
                    logger.debug(f"Error querying {lookup_type}: {e}")
                    continue

            logger.warning(f"Lookup ID {lookup_value_id} not found in common lookup types")
            return None

    except Exception as e:
        logger.error(f"Error searching for lookup ID: {e}")
        return None


if __name__ == "__main__":

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
            logger.error(f"""State file not found: {state_file}""")
            return None

        try:
            with open(state_file, "r") as f:
                all_states = json.load(f)

            if call_id not in all_states:
                logger.error(f"""No state found for call_id: {call_id}""")
                return None

            state = all_states[call_id]
            logger.info(f"""Loaded state for call_id: {call_id}""")
            return state

        except json.JSONDecodeError as e:
            logger.error(f"""Failed to parse state file: {e}""")
            return None
        except Exception as e:
            logger.error(f"""Error loading state: {e}""")
            return None

    def upload_call(call_id: str):
        state = load_state_by_call_id(call_id)
        if state is None:
            logger.error(f"""Failed to load state for call_id: {call_id}""")
            sys.exit(1)
        asyncio.run(save_intake_legalserver(state))

    def query_lookup(lookup_type: str, is_custom: bool = False):
        """Query and display lookup values for a given lookup type or UUID."""
        result = asyncio.run(query_lookup_values(lookup_type, is_custom=is_custom))
        if result is None:
            lookup_kind = "custom lookup" if is_custom else "lookup type"
            logger.error(f"Failed to query {lookup_kind}: {lookup_type}")
            sys.exit(1)

        lookup_kind = "Custom Lookup" if result["is_custom"] else "Lookup Type"
        print(f"{lookup_kind}: {result['lookup_type']}")
        print(f"Total Values: {len(result['values'])}")
        print("\nLookup Values:")
        print(json.dumps(result["values"], indent=2))

    def list_available_lookups():
        """Query and display available lookup types."""
        lookup_types = asyncio.run(get_common_lookup_types())
        if lookup_types is None:
            logger.error("Failed to retrieve available lookup types from LegalServer")
            sys.exit(1)

        print("=" * 60)
        print("Common System Lookup Types (queryable via LegalServer API)")
        print("=" * 60)
        for lookup_type in lookup_types:
            print(f"  - {lookup_type}")
        print("\nNote: Not all system lookups in the API documentation may be directly")
        print("queryable. If you get a 404 error, the lookup type may use a different")
        print("name or may not be accessible via this endpoint in your instance.")
        print("\nFor a comprehensive list of system lookup types, see:")
        print(
            "https://www.apidocs.legalserver.org/docs/ls-apis/c829022494710-search-lookup-general"
        )
        print("\n" + "=" * 60)
        print("Usage:")
        print("=" * 60)
        print("Query a specific lookup type:")
        print("  python legalserver.py --query-lookup alias_type")
        print("\nDiscover custom lookups in your instance:")
        print("  python legalserver.py --query-custom-lookups")
        print("\nThen query a custom lookup by its UUID:")
        print("  python legalserver.py --query-custom-lookup <uuid>")

    def query_custom_lookups():
        """Query and display custom lookup tables available in the LegalServer instance."""
        result = asyncio.run(get_custom_lookups())
        if result is None:
            logger.error("Failed to retrieve custom lookups from LegalServer")
            sys.exit(1)

        print(json.dumps(result, indent=2))

    # Parse command line arguments
    if len(sys.argv) < 2:
        print(
            "Usage: python legalserver.py <command> [args]\n"
            "Commands:\n"
            "  --upload <call_id>              Upload intake data for a call to LegalServer\n"
            "  --query-lookup [type]           Query system lookup values by type\n"
            "                                  Omit type to see common lookup types\n"
            "                                  Examples: alias_type, income_type, note_type\n"
            "  --query-custom-lookup <uuid>    Query a specific custom lookup by UUID\n"
            "  --query-custom-lookups          List all custom lookup tables\n"
            "                                  in your LegalServer instance"
        )
        sys.exit(1)

    command = sys.argv[1]

    if command == "--upload":
        if len(sys.argv) < 3:
            print("Usage: python legalserver.py --upload <call_id>")
            sys.exit(1)
        call_id = sys.argv[2]
        upload_call(call_id)
    elif command == "--query-lookup":
        if len(sys.argv) < 3:
            # No lookup type provided, show available types
            list_available_lookups()
        else:
            lookup_type = sys.argv[2]
            query_lookup(lookup_type, is_custom=False)
    elif command == "--query-custom-lookup":
        if len(sys.argv) < 3:
            print("Usage: python legalserver.py --query-custom-lookup <uuid>")
            sys.exit(1)
        lookup_uuid = sys.argv[2]
        query_lookup(lookup_uuid, is_custom=True)
    elif command == "--query-custom-lookups":
        query_custom_lookups()
    else:
        print(f"Unknown command: {command}")
        print(
            "Usage: python legalserver.py <command> [args]\n"
            "Commands:\n"
            "  --upload <call_id>              Upload intake data for a call to LegalServer\n"
            "  --query-lookup [type]           Query system lookup values by type\n"
            "  --query-custom-lookup <uuid>    Query a specific custom lookup by UUID\n"
            "  --query-custom-lookups          List all custom lookup tables"
        )
        sys.exit(1)
