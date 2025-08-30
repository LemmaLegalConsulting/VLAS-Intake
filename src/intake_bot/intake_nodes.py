import asyncio
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
        # case_type: conflict_check
        self.case_types = {
            "bankruptcy": {
                "conflict_check": True,
                "domestic_violence": "yes",
            },
            "citation": {
                "conflict_check": False,
                "domestic_violence": "no",
            },
            "divorce": {
                "conflict_check": True,
                "domestic_violence": "ask",
            },
            "domestic violence": {
                "conflict_check": True,
                "domestic_violence": "yes",
            },
        }

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

    async def check_case_type(self, case_type: str) -> tuple[bool, bool]:
        """Check if the caller's legal problem is a type of case that we can handle."""

        # Simulate API call delay
        await asyncio.sleep(0.5)

        is_eligible = case_type in self.case_types
        conflict_check_required = self.case_types.get(case_type, {}).get("conflict_check")
        domestic_violence = self.case_types.get(case_type, {}).get("domestic_violence")
        return is_eligible, conflict_check_required, domestic_violence

    async def check_service_area(self, caller_area: str) -> str:
        """Check if the caller's location or legal problem occurred in an eligible service area based on the city or county name."""

        match = process.extractOne(
            caller_area, self.service_area_names, scorer=fuzz.WRatio, score_cutoff=50, processor=utils.default_process
        )
        if match:
            return match[0]
        else:
            return ""

    async def conflict_check(self, opposing_party_members: list[str]) -> bool:
        """Check for conflict of interest with the caller's case."""
        if "Jimmy Dean" in opposing_party_members:
            return True
        else:
            return False


# Initialize mock system
remote_system = MockRemoteSystem()


######################################################################
# Flow
######################################################################


# MODIFIED NODE FOR TESTING
def node_initial() -> NodeConfig:
    """Create initial node for welcoming the caller. Allow the conversation to be ended."""
    return {
        **prompts.get("primary_role_message"),
        **prompts.get("collect_name_full"),
        "functions": [collect_name_full, caller_ended_conversation],
    }


# ACTUAL INITIAL NODE
# def node_initial() -> NodeConfig:
#     """Create initial node for welcoming the caller. Allow the conversation to be ended."""
#     return {
#         **prompts.get("primary_role_message"),
#         **prompts.get("initial"),
#         "functions": [initial_phone_number, caller_ended_conversation],
#     }


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
        flow_manager.state["phone"] = phone
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
        flow_manager.state["phone"] = phone
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
        next_node = node_partial_reset_with_summary() | collect_name_full()
    else:
        next_node = node_collect_phone_number()
    return None, next_node


def node_collect_name_full() -> NodeConfig:
    return {
        **prompts.get("collect_name_full"),
        "functions": [
            collect_name_full,
        ],
    }


class ResultNameFull(FlowResult):
    status: str
    first: str
    middle: str
    last: str


async def collect_name_full(
    flow_manager: FlowManager, first: str, middle: str, last: str
) -> tuple[ResultNameFull, NodeConfig]:
    """
    Record the caller's name.

    Args:
        first (str): The caller's first name.
        middle (str): The caller's middle name.
        last (str): The caller's last name.
    """

    first = first.strip()
    middle = middle.strip()
    last = last.strip()

    full = f"{first}{' ' + middle if middle else ''} {last}"
    logger.debug(f"""Full Name: {full}""")

    status = status_helper(full)
    result = ResultNameFull(status=status, first=first, middle=middle, last=last)
    if status == "success":
        flow_manager.state["caller name first"] = first
        flow_manager.state["caller name middle"] = middle
        flow_manager.state["caller name last"] = last
        next_node = node_confirm_name_full()
    else:
        next_node = node_collect_name_full()
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
    Confirm the caller's name and spelling.

    Args:
        confirmation (bool): The caller's confirmation that we have the right information.
    """
    status = status_helper(confirmation)
    if status == "success":
        next_node = node_partial_reset_with_summary() | node_collect_service_area()
    else:
        next_node = node_collect_name_correction()
    return None, next_node


def node_collect_name_correction() -> NodeConfig:
    return {
        **prompts.get("collect_name_correction"),
        "functions": [
            collect_name_full,
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


async def collect_service_area(flow_manager: FlowManager, service_area: str) -> tuple[ServiceAreaResult, NodeConfig]:
    """
    Record the service area.

    Args:
        service_area (str): The location of the caller or the legal incident. Must be a city or county.
    """

    match = await remote_system.check_service_area(service_area)
    if match == service_area:
        is_eligible = True
    else:
        is_eligible = False

    status = status_helper(is_eligible)
    result = ServiceAreaResult(status=status, service_area=service_area, is_eligible=is_eligible, match=match)

    if status == "success":
        flow_manager.state["service area"] = service_area
        next_node = node_collect_case_type()
    else:
        if match:
            next_node = node_confirm_service_area(match=match)
        else:
            alternate_providers = await remote_system.get_alternative_providers()
            next_node = node_no_service(
                alternate_providers=alternate_providers,
                no_service_reason="not in service area",
            )

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
    conflict_check_required: bool
    domestic_violence: str


async def collect_case_type(flow_manager: FlowManager, case_type: str) -> tuple[CaseTypeResult, NodeConfig]:
    """
    Check eligibility of caller's type of case.

    Args:
        case_type (str): The type of legal case that the caller has.
    """

    is_eligible, conflict_check_required, domestic_violence = await remote_system.check_case_type(case_type=case_type)

    status = status_helper(is_eligible)
    result = CaseTypeResult(
        status=status, case_type=case_type, is_eligible=is_eligible, conflict_check_required=conflict_check_required
    )

    if status == "success":
        flow_manager.state["case type"] = case_type
        flow_manager.state["domestic violence"] = domestic_violence
        if conflict_check_required:
            next_node = node_conflict_check()
        else:
            next_node = node_intake_confirmation()
    else:
        alternate_providers = await remote_system.get_alternative_providers()
        next_node = node_no_service(
            alternate_providers=alternate_providers,
            no_service_reason="ineligible case type",
        )

    return result, next_node


class ConflictCheckResult(FlowResult):
    status: str
    there_is_a_conflict: bool


def node_conflict_check() -> NodeConfig:
    return {
        **prompts.get("conflict_check"),
        "functions": [
            conflict_check,
        ],
    }


async def conflict_check(
    flow_manager: FlowManager, opposing_party_members: list[str]
) -> tuple[CaseTypeResult, NodeConfig]:
    """
    Check for conflicts of interest with the caller's case.

    Args:
        opposing_party_members (list[str]): The members of the opposing party.
    """

    # TODO: Need to see what LegalServer's conflict-check API looks like;
    # may need to ask for other related names, not just adverse;
    # may need to perform additional searches/checks for the caller
    # to see if they had previous cases that might disqualify.
    # Probably flag as "potential conflict" and pass them on in many cases.

    there_is_a_conflict = await remote_system.conflict_check(opposing_party_members=opposing_party_members)

    status = status_helper(not there_is_a_conflict)
    result = ConflictCheckResult(status=status, there_is_a_conflict=there_is_a_conflict)

    if status == "success":
        flow_manager.state["conflict"] = there_is_a_conflict
        if flow_manager.state.get("domestic violence") == "ask":
            next_node = node_collect_domestic_violence()
        else:
            next_node = node_intake_confirmation()
    else:
        alternate_providers = await remote_system.get_alternative_providers()
        next_node = node_no_service(
            alternate_providers=alternate_providers,
            no_service_reason="there is a representation conflict",
        )

    return result, next_node


class DomesticViolenceResult(FlowResult):
    status: str
    experiencing_domestic_violence: bool


def node_collect_domestic_violence() -> NodeConfig:
    return {
        **prompts.get("collect_domestic_violence"),
        "functions": [
            collect_domestic_violence,
        ],
    }


async def collect_domestic_violence(
    flow_manager: FlowManager, experiencing_domestic_violence: bool
) -> tuple[None, NodeConfig]:
    """
    Record if the caller experiencing domestic violence or not.

    Args:
        experiencing_domestic_violence (bool): The caller's answer that they are or are not experiencing domestic violence.
    """
    flow_manager.state["domestic violence"] = experiencing_domestic_violence

    result = ConflictCheckResult(status="success", experiencing_domestic_violence=experiencing_domestic_violence)

    next_node = node_partial_reset_with_summary() | node_intake_confirmation()

    return result, next_node


def node_intake_confirmation() -> NodeConfig:
    """Create confirmation node for successful intake."""
    return {
        **prompts.get("intake_confirmation"),
        "functions": [
            end_conversation,
        ],
    }


def node_no_service(alternate_providers: list[str], no_service_reason: str) -> NodeConfig:
    """Create node for handling ineligibility."""
    alternate_providers_list = ", ".join(alternate_providers)
    return {
        **prompts.get(
            "no_service", alternate_providers_list=alternate_providers_list, no_service_reason=no_service_reason
        ),
        "functions": [
            end_conversation,
        ],
    }


async def end_conversation(flow_manager: FlowManager) -> tuple[None, NodeConfig]:
    """End the conversation."""
    return None, node_end_conversation()


def node_end_conversation() -> NodeConfig:
    """Create the final node."""
    return {
        **prompts.get("end"),
        "functions": [],
        "post_actions": [{"type": "end_conversation"}],
    }


async def caller_ended_conversation(flow_manager: FlowManager) -> tuple[None, NodeConfig]:
    """The caller ended the conversation."""
    return None, node_caller_ended_conversation()


def node_caller_ended_conversation() -> NodeConfig:
    """Create the final node."""
    return {
        **prompts.get("caller_ended_conversation"),
        "functions": [],
        "post_actions": [{"type": "end_conversation"}],
    }


######################################################################
# Utility node configurations
######################################################################


def node_partial_reset_with_summary() -> NodeConfig:
    return {
        **prompts.get("primary_role_message"),
        "context_strategy": ContextStrategyConfig(
            strategy=ContextStrategy.RESET_WITH_SUMMARY,
            summary_prompt=prompts.get("reset_with_summary"),
        ),
    }
