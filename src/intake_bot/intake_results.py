from pipecat_flows import (
    FlowResult,
)


class AssetsResult(FlowResult):
    is_eligible: bool


class CaseTypeResult(FlowResult):
    case_type: str
    is_eligible: bool


class CitizenshipResult(FlowResult):
    has_citizenship: bool


class ConflictCheckResult(FlowResult):
    there_is_a_conflict: bool


class DomesticViolenceResult(FlowResult):
    experiencing_domestic_violence: bool


class EmergencyResult(FlowResult):
    is_emergency: bool


class IncomeResult(FlowResult):
    is_eligible: bool
    monthly_income: int
    poverty_percent: int


class NameResult(FlowResult):
    first: str
    middle: str
    last: str


class PhoneNumberResult(FlowResult):
    phone: str


class ServiceAreaResult(FlowResult):
    service_area: str
    is_eligible: bool
    match: str
