from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, RootModel

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
