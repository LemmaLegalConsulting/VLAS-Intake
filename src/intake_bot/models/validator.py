from datetime import date
from enum import Enum
from typing import List, Optional

from intake_bot.services.phonenumber import phone_number_is_valid
from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator

######################################################################
# Caller Names
######################################################################


class NameTypeValue(str, Enum):
    """LegalServer alias_type lookup values for additional names. Matches the `lookup_value_name`."""

    FORMER_NAME = "Former Name"
    MAIDEN_NAME = "Maiden Name"
    NICKNAME = "Nickname"
    LEGAL_NAME = "Legal Name"
    OTHER = "Other"


class CallerName(BaseModel):
    first: str
    middle: Optional[str] = None
    last: str
    type: NameTypeValue = NameTypeValue.FORMER_NAME

    model_config = ConfigDict(use_enum_values=True)

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

    @field_validator("root", mode="after")
    @classmethod
    def no_duplicate_names(cls, names: List[CallerName]) -> List[CallerName]:
        """Ensure no duplicate CallerName entries exist (all fields the same)."""
        seen = set()
        for name in names:
            # Create a tuple of all fields for comparison
            name_tuple = (name.first, name.middle, name.last)
            if name_tuple in seen:
                raise ValueError(
                    f"Duplicate CallerName entry found: {name.first} {name.middle or ''} {name.last}".strip()
                )
            seen.add(name_tuple)
        return names


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

    @field_validator("period", mode="after")
    @classmethod
    def normalize_zero_income_period(cls, v, info):
        """When income amount is 0, normalize period to 'Monthly' for consistency."""
        if info.data.get("amount") == 0:
            return IncomePeriod.MONTHLY
        return v


class MemberIncome(RootModel[dict[str, IncomeDetail]]):  # income_category_name -> IncomeDetail
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
# Phone Types
######################################################################


class PhoneType(str, Enum):
    """Phone types for caller/contact - uses {type}_phone field naming."""

    WORK = "work"
    OTHER = "other"
    HOME = "home"
    MOBILE = "mobile"
    FAX = "fax"


class PhoneTypeAdverseParty(str, Enum):
    """Phone types for adverse parties - uses phone_{type} field naming."""

    BUSINESS = "business"
    HOME = "home"
    MOBILE = "mobile"
    FAX = "fax"


######################################################################
# Adverse Parties
######################################################################


class Phone(BaseModel):
    number: str
    type: PhoneTypeAdverseParty

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
