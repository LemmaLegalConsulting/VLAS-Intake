import asyncio
import re
from itertools import chain
from textwrap import dedent
from typing import Literal
from loguru import logger
from pipecat_flows import (
    FlowManager,
    FlowResult,
    NodeConfig,
)


def status_helper(status: bool) -> Literal["success", "failure"]:
    """Helper for FlowResult's _status_ value."""
    return "success" if status else "failure"


class MockRemoteSystem:
    """Simulates a remote system API."""

    def __init__(self):
        self.service_area_names = [
            "Amelia County",
            "Amherst County",
            "Appomattox County",
            "Bedford County",
            "Brunswick County",
            "Campbell County",
            "Danville City",
            "Emporia City",
            "Franklin City",
            "Greensville County",
            "Halifax County",
            "Henry County",
            "Isle of Wight County",
            "Lunenburg County",
            "Lynchburg City",
            "Martinsville City",
            "Mecklenburg County",
            "Nottoway County",
            "Patrick County",
            "Pittsylvania County",
            "Prince Edward County",
            "South Boston",
            "Southampton County",
            "Suffolk City",
            "Sussex County",
        ]
        self.service_area_zip_codes = [
            22951,
            23002,
            23004,
            23027,
            23040,
            23083,
            23123,
            23304,
            23314,
            23315,
            23397,
            23424,
            23430,
            23432,
            23433,
            23434,
            23435,
            23436,
            23437,
            23438,
            23481,
            23487,
            23821,
            23824,
            23827,
            23828,
            23829,
            23837,
            23843,
            23844,
            23845,
            23847,
            23851,
            23856,
            23857,
            23866,
            23867,
            23868,
            23874,
            23876,
            23878,
            23879,
            23887,
            23888,
            23889,
            23890,
            23893,
            23897,
            23898,
            23901,
            23915,
            23917,
            23919,
            23920,
            23921,
            23922,
            23923,
            23924,
            23927,
            23930,
            23934,
            23936,
            23937,
            23938,
            23942,
            23944,
            23947,
            23950,
            23952,
            23954,
            23958,
            23959,
            23960,
            23962,
            23963,
            23964,
            23966,
            23967,
            23968,
            23970,
            23973,
            23974,
            23976,
            24053,
            24054,
            24055,
            24069,
            24076,
            24078,
            24082,
            24089,
            24112,
            24120,
            24133,
            24139,
            24148,
            24161,
            24165,
            24168,
            24171,
            24177,
            24185,
            24501,
            24502,
            24503,
            24504,
            24514,
            24515,
            24517,
            24520,
            24521,
            24522,
            24527,
            24528,
            24529,
            24530,
            24531,
            24533,
            24534,
            24535,
            24538,
            24539,
            24540,
            24541,
            24543,
            24544,
            24549,
            24550,
            24554,
            24557,
            24558,
            24563,
            24565,
            24566,
            24569,
            24571,
            24572,
            24574,
            24576,
            24577,
            24580,
            24585,
            24586,
            24588,
            24589,
            24592,
            24593,
            24594,
            24595,
            24596,
            24597,
            24598,
        ]
        self.ineligible_case_types = [
            "criminal",
            "traffic",
            "injury",
        ]

    async def valid_phone_number(self, phone_number: str) -> tuple[bool, str]:
        """Cleans and checks that the provided number is a (basically) valid phone number."""
        valid: bool = False
        digits_only: str = re.sub(r"\D", "", phone_number)
        if len(digits_only) == 10:
            phone_number = f"""{digits_only[:3]}-{digits_only[3:6]}-{digits_only[6:]}"""
            valid = True
        return valid, phone_number

    async def get_alternative_providers(self) -> list[str]:
        """Alternative legal providers for the customer."""
        alternatives = [
            "Center for Legal Help",
            "Local Legal Help",
        ]
        return alternatives

    async def check_case_type(self, case_type: str) -> tuple[bool, list[str]]:
        """Check if the customer's legal problem is a type of case that we can handle."""

        # Simulate API call delay
        await asyncio.sleep(0.5)

        is_eligible = case_type not in self.ineligible_case_types
        return is_eligible

    async def check_service_area(self, customer_area: str) -> list[str]:
        """Check if the customer's location or legal problem occurred in an eligible service area based on the city name, county name, or zip code."""

        # Simulate API call delay
        await asyncio.sleep(0.5)

        # Check for rough matches
        simple_area = customer_area.lower().replace("city", "").replace("county", "").replace("town", "").strip()
        matches = [
            match
            for match in chain(self.service_area_names, self.service_area_zip_codes)
            if simple_area in str(match).lower()
        ]
        return matches


# Initialize mock system
remote_system = MockRemoteSystem()


# Function handlers


class NameAndPhoneResult(FlowResult):
    status: str
    name: str
    phone: str


async def collect_name_and_phone(
    flow_manager: FlowManager, name: str, phone: str
) -> tuple[NameAndPhoneResult, NodeConfig]:
    """
    Record the customer's name and phone number.

    Args:
        name (str): The customer's first and last name.
        phone (str): The customer's phone number as text, starting with the 3 digit area code followed by the 7 digit phone number.
    """

    valid_phone, phone = await remote_system.valid_phone_number(phone_number=phone)

    logger.debug(f"""Name: {name}""")
    logger.debug(f"""Phone: {phone}""")
    logger.debug(f"""Valid: {valid_phone}""")

    status = status_helper(valid_phone)
    result = NameAndPhoneResult(status=status, name=name, phone=phone)

    if status == "success":
        next_node = create_node_service_area()
    else:
        next_node = create_node_initial()

    return result, next_node


class ServiceAreaResult(FlowResult):
    status: str
    service_area: str
    is_eligible: bool
    matches: list[str]


async def collect_service_area(flow_manager: FlowManager, customer_area: str) -> tuple[ServiceAreaResult, NodeConfig]:
    """
    Record the customer's location or the location of the incident.

    Args:
        customer_area (str): The location of the customer or the legal incident. Must be a city, county, or zip code.
    """

    is_eligible = False

    matches = await remote_system.check_service_area(customer_area)

    if len(matches) == 1 and matches[0] == customer_area:
        is_eligible = True

    status = status_helper(is_eligible)
    result = ServiceAreaResult(status=status, service_area=customer_area, is_eligible=is_eligible, matches=matches)

    if status == "success":
        next_node = create_node_case_type()
    else:
        if matches:
            next_node = create_node_service_area_matches(matches=matches)
        else:
            next_node = create_node_no_service(await remote_system.get_alternative_providers())

    return result, next_node


class CaseTypeResult(FlowResult):
    status: str
    case_type: str
    is_eligible: bool


async def collect_case_type(flow_manager: FlowManager, case_type: str) -> tuple[CaseTypeResult, NodeConfig]:
    """
    Check eligibility of customer's type of case.

    Args:
        case_type (str): The type of legal case that the customer has.
    """

    is_eligible = await remote_system.check_case_type(case_type=case_type)

    alternate_providers = await remote_system.get_alternative_providers() if not is_eligible else []

    status = status_helper(is_eligible)
    result = CaseTypeResult(
        status=status, case_type=case_type, is_eligible=is_eligible, alternate_providers=alternate_providers
    )

    if status == "success":
        next_node = create_node_confirmation()
    else:
        next_node = create_node_no_service(await remote_system.get_alternative_providers())

    return result, next_node


async def end_conversation(flow_manager: FlowManager) -> tuple[None, NodeConfig]:
    """End the conversation."""
    return None, create_node_end()


# Node configurations
def create_node_initial() -> NodeConfig:
    """Create initial node for greeting, and name and phone number collection."""
    return {
        "name": "initial",
        "role_messages": [
            {
                "role": "system",
                "content": """You are Ada, an agent for Virginia's Law-Line Legal Help Service. Be concise, professional, and friendly. This is a voice conversation, so avoid special characters and emojis.""",
            }
        ],
        "task_messages": [
            {
                "role": "system",
                "content": """Greet the customer and ask for their name and phone number. This is your only job for now; if the customer asks for something else, politely remind them you can't do it.""",
            }
        ],
        "functions": [
            collect_name_and_phone,
        ],
    }


def create_node_service_area() -> NodeConfig:
    """Create node for getting and checking the service area."""
    logger.debug("Creating service area node")
    return {
        "name": "service_area",
        "task_messages": [
            {
                "role": "system",
                "content": """Ask the customer where the legal incident occurred.""",
            }
        ],
        "functions": [
            collect_service_area,
        ],
    }


def create_node_service_area_matches(matches: list[str]) -> NodeConfig:
    """Create node for confirming the possible matches for the service area."""
    logger.debug("Creating service area matches node")
    matches_list = ", ".join(matches)
    return {
        "name": "service_area_matches",
        "task_messages": [
            {
                "role": "system",
                "content": dedent(f"""\
                    Tell the customer that you did not find an exact match for their service area. Read the following possible matches: {matches_list}. 
                    Ask the customer to confirm if any of these matches are correct:
                    - If the customer confirms one of the matches is correct, re-check that service area and continue intake.
                    - If the customer says none of the matches are correct, end the conversation.
                    - If the customer wants to make a correction or provide a different service area, ask them to provide their service area again.
                    """),
            }
        ],
        "functions": [
            collect_service_area,
            end_conversation,
        ],
    }


def create_node_case_type() -> NodeConfig:
    """Create node for getting and checking the case type."""
    logger.debug("Creating node case_type")
    return {
        "name": "case_type",
        "task_messages": [
            {
                "role": "system",
                "content": """Ask the customer what kind of legal case they have.""",
            }
        ],
        "functions": [
            collect_case_type,
        ],
    }


def create_node_confirmation() -> NodeConfig:
    """Create confirmation node for successful intake."""
    return {
        "name": "confirmation",
        "task_messages": [
            {
                "role": "system",
                "content": """Confirm the intake details and ask if they need anything else. When reading back the customer's phone number, speak each number clearly and pause briefly at, but don't pronounce, each hyphen or period. For example, if the number is 123-456-7890, say: "One two three. Four five six. Seven eight nine zero." """,
            }
        ],
        "functions": [
            end_conversation,
        ],
    }


def create_node_no_service(alternate_providers: list[str]) -> NodeConfig:
    """Create node for handling ineligibility."""
    alternate_providers_list = ", ".join(alternate_providers)
    return {
        "name": "no_service",
        "task_messages": [
            {
                "role": "system",
                "content": (
                    f"""Apologize that the customer's location isn't within the service area. Suggest these alternate providers: {alternate_providers_list}."""
                ),
            }
        ],
        "functions": [
            end_conversation,
        ],
    }


def create_node_end() -> NodeConfig:
    """Create the final node."""
    return {
        "name": "end",
        "task_messages": [
            {
                "role": "system",
                "content": """Thank them and end the conversation.""",
            }
        ],
        "functions": [],
        "post_actions": [{"type": "end_conversation"}],
    }
