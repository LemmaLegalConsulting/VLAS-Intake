from enum import Enum

from intake_bot.models.validator import (
    Address,
    AdverseParties,
    Assets,
    CallerNames,
    HouseholdIncome,
    HouseholdMembers,
    PhoneTypeCaller,
)
from pydantic import BaseModel, ConfigDict, Field


class Status(str, Enum):
    SUCCESS = "success"
    ERROR = "error"


class IntakeFlowResult(BaseModel):
    status: Status
    error: str | None = None

    model_config = ConfigDict(use_enum_values=True)


class AddressResult(IntakeFlowResult):
    address: Address | None = None


class AdversePartiesResult(IntakeFlowResult):
    adverse_parties: AdverseParties


class AssetsResult(IntakeFlowResult):
    is_eligible: bool
    listing: Assets
    total_value: int
    receives_benefits: bool


class AssetCategoryResult(IntakeFlowResult):
    listing: Assets


class CallerNamesResult(IntakeFlowResult):
    names: CallerNames


class CaseTypeResult(IntakeFlowResult):
    is_eligible: bool
    legal_problem_code: str
    case_description: str


class CitizenshipResult(IntakeFlowResult):
    is_citizen: bool


class DateOfBirthResult(IntakeFlowResult):
    date_of_birth: str


class DomesticViolenceResult(IntakeFlowResult):
    is_experiencing: bool


class HouseholdCompositionResult(IntakeFlowResult):
    number_of_adults: int
    number_of_children: int


class HouseholdMembersResult(IntakeFlowResult):
    members: HouseholdMembers


class IncomeResult(IntakeFlowResult):
    is_eligible: bool
    monthly_amount: int
    listing: HouseholdIncome
    household_size: int


class LanguageResult(IntakeFlowResult):
    language: str


class PhoneNumberResult(IntakeFlowResult):
    is_valid: bool
    phone_number: str
    phone_type: PhoneTypeCaller | None = None


class ServiceAreaResult(IntakeFlowResult):
    location: str | None = None
    is_eligible: bool | None = None
    fips_code: int | None = None
    outcome: str = "unknown"
    candidates: list[str] = Field(default_factory=list)
    match_type: str | None = None


class SSNLast4Result(IntakeFlowResult):
    ssn_last_4: str
