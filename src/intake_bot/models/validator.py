from datetime import date
from enum import Enum
from typing import List, Optional

from intake_bot.services.phonenumber import phone_number_is_valid
from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator

######################################################################
# Caller Names
######################################################################


class CallerName(BaseModel):
    first: str
    middle: Optional[str] = None
    last: str

    @field_validator("first", "last", mode="before")
    @classmethod
    def strip_and_validate_required_names(cls, v):
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("First and last names are required and cannot be empty")
        return v

    @field_validator("middle", mode="before")
    def strip_and_normalize_middle(cls, v):
        if isinstance(v, str):
            v = v.strip() or None
        return v


class CallerNames(RootModel[List[CallerName]]):
    """A list of CallerName objects."""

    pass


######################################################################
# Income
######################################################################


class IncomePeriod(str, Enum):
    """LegalServer income period values."""

    ANNUALLY = "Annually"
    MONTHLY = "Monthly"
    WEEKLY = "Weekly"
    BIWEEKLY = "Biweekly"
    SEMI_MONTHLY = "Semi-Monthly"
    QUARTERLY = "Quarterly"


class IncomeDetail(BaseModel):
    amount: int = Field(..., description="The amount of income received.")
    period: IncomePeriod = Field(
        ...,
        description="The period for the income: Annually, Monthly, Weekly, Biweekly, Semi-Monthly, or Quarterly.",
    )


class MemberIncome(RootModel[dict[int, IncomeDetail]]):  # income_category_id -> IncomeDetail
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


######################################################################
# Adverse Parties
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
