from datetime import date
from enum import Enum
from typing import Any, Dict, List, Optional

from intake_bot.services.phonenumber import phone_number_is_valid
from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator

######################################################################
# Conflict Check
######################################################################


class PhoneType(str, Enum):
    BUSINESS = "business"
    OTHER = "other"
    HOME = "home"
    MOBILE = "mobile"
    FAX = "fax"


class Phone(BaseModel):
    number: str
    type: PhoneType

    @field_validator("number", mode="after")
    @classmethod
    def validate_phone_number(cls, v):
        is_valid, formatted = phone_number_is_valid(v)
        if not is_valid:
            raise ValueError(f"""Invalid US phone number: {v}""")
        return formatted

    model_config = ConfigDict(use_enum_values=True)


class AdverseParty(BaseModel):
    first: str
    middle: Optional[str] = None
    last: str
    dob: Optional[date] = None
    phones: Optional[List[Phone]] = None

    @field_validator("middle", "dob", "phones", mode="before")
    def falsy_to_none(cls, v):
        if not v:
            return None
        return v


class AdverseParties(RootModel[List[AdverseParty]]):
    """A list of AdverseParty objects."""

    pass


######################################################################
# Classification
######################################################################


class Label(BaseModel):
    """A predicted taxonomy label with optional confidence."""

    label: str
    confidence: Optional[float] = None
    legal_problem_code: Optional[str] = None


class FollowUpQuestion(BaseModel):
    """A follow-up question to refine classification."""

    question: str
    format: Optional[str] = None
    options: Optional[List[str]] = None


class ClassificationResponse(BaseModel):
    """Response payload with aggregated labels and follow-up questions."""

    labels: List[Label]
    follow_up_questions: List[FollowUpQuestion]

    # Debug fields, populated when include_debug_details is True
    raw_provider_results: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Raw results from each classifier provider (debug mode only)",
    )
    weighted_label_scores: Optional[Dict[str, float]] = Field(
        default=None, description="Weighted scores for each label (debug mode only)"
    )
    weighted_question_scores: Optional[Dict[str, float]] = Field(
        default=None, description="Weighted scores for each question (debug mode only)"
    )


######################################################################
# Income
######################################################################


class IncomePeriod(str, Enum):
    month = "month"
    year = "year"


class IncomeDetail(BaseModel):
    amount: int = Field(..., description="The amount of income received.")
    period: IncomePeriod = Field(
        ..., description='The period for the income, either "month" or "year".'
    )


class MemberIncome(RootModel[dict[str, IncomeDetail]]):  # income_type -> IncomeDetail
    pass


class HouseholdIncome(RootModel[dict[str, MemberIncome]]):  # person_name -> MemberIncome
    pass


######################################################################
# Asset
######################################################################


class AssetEntry(RootModel[dict[str, int]]):  # asset_name -> net present value (int)
    """
    Represents a single asset entry mapping an asset name to its integer value.
    Example: {"car": 5000}
    """

    pass


class Assets(RootModel[list[AssetEntry]]):
    """
    Represents the overall assets list as a Pydantic RootModel wrapping a list of AssetEntry.
    Example:
        [
            {"car": 5000},
            {"savings": 2000}
        ]
    """

    pass
