from enum import Enum

from intake_bot.models.validator import AdverseParties, CallerNames
from pydantic import BaseModel, ConfigDict


class Status(str, Enum):
    SUCCESS = "success"
    ERROR = "error"


class IntakeFlowResult(BaseModel):
    status: Status
    error: str = None

    model_config = ConfigDict(use_enum_values=True)


class AdversePartiesResult(IntakeFlowResult):
    adverse_parties: AdverseParties


class AssetsResult(IntakeFlowResult):
    is_eligible: bool
    listing: list
    total_value: int
    receives_benefits: bool


class CallerNamesResult(IntakeFlowResult):
    names: CallerNames


class CaseTypeResult(IntakeFlowResult):
    is_eligible: bool
    legal_problem_code: str


class CitizenshipResult(IntakeFlowResult):
    is_citizen: bool


class DomesticViolenceResult(IntakeFlowResult):
    is_experiencing: bool
    perpetrators: list[str]


class EmergencyResult(IntakeFlowResult):
    is_emergency: bool


class IncomeResult(IntakeFlowResult):
    is_eligible: bool
    monthly_amount: int
    listing: dict
    household_size: int


class PhoneNumberResult(IntakeFlowResult):
    is_valid: bool
    phone_number: str


class ServiceAreaResult(IntakeFlowResult):
    location: str
    is_eligible: bool
    fips_code: int
