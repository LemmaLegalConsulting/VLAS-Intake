import asyncio
import re
from typing import Literal

import phonenumbers
from loguru import logger
from pipecat_flows import (
    ContextStrategy,
    ContextStrategyConfig,
    FlowManager,
    FlowResult,
    NodeConfig,
)
from rapidfuzz import fuzz, process, utils

from .prompts import Prompts


def status_helper(status: bool) -> Literal["success", "failure"]:
    """Helper for FlowResult's _status_ value."""
    return "success" if status else "failure"


# Initialize Prompts
prompts = Prompts()


######################################################################
# MockRemoteSystem
######################################################################


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
        self.ineligible_case_types = [
            "criminal",
            "traffic",
            "injury",
        ]

    async def valid_phone_number(self, phone: str) -> tuple[bool, str]:
        try:
            phone_number = phonenumbers.parse(phone, "US")
            valid = phonenumbers.is_valid_number(phone_number)
            if valid:
                phone = phonenumbers.format_number(phone_number, phonenumbers.PhoneNumberFormat.NATIONAL)
        except phonenumbers.phonenumberutil.NumberParseException:
            valid = False
        return valid, phone

    async def get_alternative_providers(self) -> list[str]:
        """Alternative legal providers for the caller."""
        alternatives = [
            "Center for Legal Help",
            "Local Legal Help",
        ]
        return alternatives

    async def check_case_type(self, case_type: str) -> tuple[bool, list[str]]:
        """Check if the caller's legal problem is a type of case that we can handle."""

        # Simulate API call delay
        await asyncio.sleep(0.5)

        is_eligible = case_type not in self.ineligible_case_types
        return is_eligible

    async def check_service_area(self, caller_area: str) -> str:
        """Check if the caller's location or legal problem occurred in an eligible service area based on the city or county name."""

        match = process.extractOne(
            caller_area, self.service_area_names, scorer=fuzz.WRatio, score_cutoff=50, processor=utils.default_process
        )
        if match:
            return match[0]
        else:
            return ""


# Initialize mock system
remote_system = MockRemoteSystem()


######################################################################
# Flow
######################################################################


def node_initial() -> NodeConfig:
    """Create initial node for welcoming the caller. Allow the conversation to be ended."""
    return {
        **prompts.get("initial"),
        "functions": [initial_phone_number, end_conversation],
    }


class ResultPhoneNumber(FlowResult):
    status: str
    phone: str


async def initial_phone_number(flow_manager: FlowManager) -> tuple[ResultPhoneNumber, NodeConfig]:
    """
    This function checks if the phone system recieved the caller's phone number. If so, confirm the number with the caller. If not, collect the caller's phone number.
    """

    logger.debug(f"""initial_phone_number (flow_manager.state["phone"]): {flow_manager.state["phone"]}""")

    valid_phone, phone = await remote_system.valid_phone_number(phone=flow_manager.state["phone"])

    logger.debug(f"""Phone: {phone}""")
    logger.debug(f"""Valid: {valid_phone}""")

    status = status_helper(valid_phone)
    result = ResultPhoneNumber(status=status, phone=phone)
    if status == "success":
        next_node = node_confirm_phone_number()
    else:
        next_node = node_collect_phone_number()
    return result, next_node


def node_collect_phone_number() -> NodeConfig:
    return {
        **prompts.get("collect_phone_number"),
        "functions": [
            collect_phone_number,
        ],
    }


async def collect_phone_number(flow_manager: FlowManager, phone: str) -> tuple[ResultPhoneNumber, NodeConfig]:
    """
    Collect the caller's phone number.

    Args:
        phone (str): The caller's 10 digit phone number.
    """

    logger.debug(f"""flow_manager.state["phone"]: {flow_manager.state["phone"]}""")

    valid_phone, phone = await remote_system.valid_phone_number(phone=phone)

    logger.debug(f"""Phone: {phone}""")
    logger.debug(f"""Valid: {valid_phone}""")

    status = status_helper(valid_phone)
    result = ResultPhoneNumber(status=status, phone=phone)
    if status == "success":
        next_node = node_confirm_phone_number()
    else:
        next_node = node_collect_phone_number()
    return result, next_node


def node_confirm_phone_number() -> NodeConfig:
    return {
        **prompts.get("confirm_phone_number"),
        "functions": [
            confirm_phone_number,
        ],
    }


async def confirm_phone_number(flow_manager: FlowManager, confirmation: bool) -> tuple[None, NodeConfig]:
    """
    Confirm the caller's phone number.

    Args:
        confirmation (bool): The caller's phone number is correct (True) or incorrect (False).
    """
    status = status_helper(confirmation)
    if status == "success":
        next_node = node_collect_name_first() | node_partial_reset_with_summary()
    else:
        next_node = node_collect_phone_number()
    return None, next_node


def node_collect_name_first() -> NodeConfig:
    return {
        **prompts.get("collect_name_first"),
        "functions": [
            collect_name_first,
        ],
    }


class ResultNameFirst(FlowResult):
    status: str
    name: str


async def collect_name_first(flow_manager: FlowManager, name: str) -> tuple[ResultNameFirst, NodeConfig]:
    """
    Record the caller's first name.

    Args:
        name (str): The caller's first name.
    """

    name = re.sub(r"\W", "", name)
    logger.debug(f"""Name First: {name}""")

    status = status_helper(name)
    result = ResultNameFirst(status=status, name=name)
    if status == "success":
        if flow_manager.state["confirming_name"]:
            next_node = node_confirm_name_full()
        else:
            next_node = node_collect_name_middle()
    else:
        next_node = node_collect_name_first()
    return result, next_node


def node_collect_name_middle() -> NodeConfig:
    return {
        **prompts.get("collect_name_middle"),
        "functions": [
            collect_name_middle,
        ],
    }


class ResultNameMiddle(FlowResult):
    status: str
    name: str


async def collect_name_middle(flow_manager: FlowManager, name: str) -> tuple[ResultNameMiddle, NodeConfig]:
    """
    Record the caller's middle name (if they have one).

    Args:
        name (str, optional): The caller's middle name.
    """

    if name:
        name = re.sub(r"\W", "", name)
    else:
        name = ""
    logger.debug(f"""Name Middle: {name}""")

    status = status_helper(True)
    result = ResultNameMiddle(status=status, name=name)
    if flow_manager.state["confirming_name"]:
        next_node = node_confirm_name_full()
    else:
        next_node = node_collect_name_last()
    return result, next_node


def node_collect_name_last() -> NodeConfig:
    return {
        **prompts.get("collect_name_last"),
        "functions": [
            collect_name_last,
        ],
    }


class ResultNameLast(FlowResult):
    status: str
    name: str


async def collect_name_last(flow_manager: FlowManager, name: str) -> tuple[ResultNameLast, NodeConfig]:
    """
    Record the caller's last name.

    Args:
        name (str): The caller's last name.
    """

    name = re.sub(r"\W", "", name)
    logger.debug(f"""Name Last: {name}""")

    status = status_helper(name)
    result = ResultNameLast(status=status, name=name)
    if status == "success":
        next_node = node_confirm_name_full()
    else:
        next_node = node_collect_name_last()
    return result, next_node


def node_confirm_name_full() -> NodeConfig:
    return {
        **prompts.get("confirm_name_full"),
        "functions": [
            confirm_name_full,
        ],
    }


async def confirm_name_full(flow_manager: FlowManager, confirmation: bool) -> tuple[None, NodeConfig]:
    """
    Confirm the caller's name.

    Args:
        confirmation (bool): The caller's confirmation that we have the right information.
    """
    flow_manager.state["confirming_name"] = True
    status = status_helper(confirmation)
    if status == "success":
        next_node = node_collect_service_area() | node_partial_reset_with_summary()
    else:
        next_node = node_collect_name_correction()
    return None, next_node


def node_collect_name_correction() -> NodeConfig:
    return {
        **prompts.get("collect_name_correction"),
        "functions": [
            collect_name_first,
            collect_name_middle,
            collect_name_last,
        ],
    }


def node_collect_service_area() -> NodeConfig:
    return {
        **prompts.get("collect_service_area"),
        "functions": [
            collect_service_area,
        ],
    }


class ServiceAreaResult(FlowResult):
    status: str
    service_area: str
    is_eligible: bool
    match: str


async def collect_service_area(flow_manager: FlowManager, caller_area: str) -> tuple[ServiceAreaResult, NodeConfig]:
    """
    Record the caller's location or the location of the incident.

    Args:
        caller_area (str): The location of the caller or the legal incident. Must be a city or county.
    """

    match = await remote_system.check_service_area(caller_area)
    if match == caller_area:
        is_eligible = True
    else:
        is_eligible = False

    status = status_helper(is_eligible)
    result = ServiceAreaResult(status=status, service_area=caller_area, is_eligible=is_eligible, match=match)

    if status == "success":
        next_node = node_collect_case_type()
    else:
        if match:
            next_node = node_confirm_service_area(match=match)
        else:
            next_node = node_no_service(await remote_system.get_alternative_providers())

    return result, next_node


def node_confirm_service_area(match: str) -> NodeConfig:
    return {
        **prompts.get("confirm_service_area", match=match),
        "functions": [
            collect_service_area,
            end_conversation,
        ],
    }


def node_collect_case_type() -> NodeConfig:
    return {
        **prompts.get("collect_case_type"),
        "functions": [
            collect_case_type,
        ],
    }


class CaseTypeResult(FlowResult):
    status: str
    case_type: str
    is_eligible: bool


async def collect_case_type(flow_manager: FlowManager, case_type: str) -> tuple[CaseTypeResult, NodeConfig]:
    """
    Check eligibility of caller's type of case.

    Args:
        case_type (str): The type of legal case that the caller has.
    """

    is_eligible = await remote_system.check_case_type(case_type=case_type)

    alternate_providers = await remote_system.get_alternative_providers() if not is_eligible else []

    status = status_helper(is_eligible)
    result = CaseTypeResult(
        status=status, case_type=case_type, is_eligible=is_eligible, alternate_providers=alternate_providers
    )

    if status == "success":
        next_node = node_intake_confirmation()
    else:
        next_node = node_no_service(await remote_system.get_alternative_providers())

    return result, next_node


def node_intake_confirmation() -> NodeConfig:
    """Create confirmation node for successful intake."""
    return {
        **prompts.get("intake_confirmation"),
        "functions": [
            end_conversation,
        ],
    }


def node_no_service(alternate_providers: list[str]) -> NodeConfig:
    """Create node for handling ineligibility."""
    alternate_providers_list = ", ".join(alternate_providers)
    return {
        **prompts.get("no_service", alternate_providers_list=alternate_providers_list),
        "functions": [
            end_conversation,
        ],
    }


async def end_conversation(flow_manager: FlowManager) -> tuple[None, NodeConfig]:
    """End the conversation."""
    return None, create_node_end()


def create_node_end() -> NodeConfig:
    """Create the final node."""
    return {
        **prompts.get("end"),
        "functions": [],
        "post_actions": [{"type": "end_conversation"}],
    }


######################################################################
# Utility node configurations
######################################################################


def node_partial_reset_with_summary() -> NodeConfig:
    return {
        "context_strategy": ContextStrategyConfig(
            strategy=ContextStrategy.RESET_WITH_SUMMARY,
            summary_prompt=prompts.get("reset_with_summary"),
        ),
    }
