import unicodedata
from datetime import date
from enum import Enum
from typing import List, Optional

from intake_bot.services.phonenumber import phone_number_is_valid
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
    model_validator,
)


def normalize_to_ascii(v: str | None) -> str | None:
    """Normalize unicode characters to their closest ASCII representation."""
    if v is None:
        return None
    if isinstance(v, str):
        return (
            unicodedata.normalize("NFKD", v).encode("ascii", "ignore").decode("ascii")
        )
    return v


######################################################################
# Address
######################################################################


class Address(BaseModel):
    street: str
    street_2: Optional[str] = None
    city: str
    state: str
    zip: str
    county: str

    @field_validator(
        "street", "street_2", "city", "state", "zip", "county", mode="before"
    )
    @classmethod
    def normalize_address_fields(cls, v):
        return normalize_to_ascii(v)

    @field_validator("street", "city", "state", "zip", "county", mode="after")
    @classmethod
    def validate_required_fields(cls, v):
        if not v or not v.strip():
            raise ValueError("This field is required and cannot be empty")
        return v.strip()

    @field_validator("county", mode="after")
    @classmethod
    def clean_county(cls, v: str) -> str:
        if v.lower().endswith(" county"):
            return v[: -len(" county")].strip()
        return v

    @field_validator("street_2", mode="before")
    def falsy_to_none(cls, v):
        if not v:
            return None
        return v

    model_config = ConfigDict(use_enum_values=True)


######################################################################
# Adverse Parties
######################################################################


class PhoneTypeAdverseParty(str, Enum):
    """Phone types for adverse parties - uses phone_{type} field naming."""

    BUSINESS = "business"
    HOME = "home"
    MOBILE = "mobile"
    FAX = "fax"


class PhoneAdverseParty(BaseModel):
    number: str
    type: Optional[PhoneTypeAdverseParty] = None

    @field_validator("number", mode="before")
    @classmethod
    def normalize_phone(cls, v):
        return normalize_to_ascii(v)

    @field_validator("number", mode="after")
    @classmethod
    def validate_phone_number(cls, v):
        is_valid, formatted = phone_number_is_valid(v)
        if not is_valid:
            raise ValueError(f"""Invalid US phone number: {v}""")
        return formatted

    @field_validator("type", mode="before")
    @classmethod
    def falsy_type_to_none(cls, v):
        if not v:
            return None
        return v

    model_config = ConfigDict(use_enum_values=True)


class AdverseParty(BaseModel):
    first: str
    middle: Optional[str] = None
    last: str
    suffix: Optional[str] = None
    dob: Optional[date] = None
    phones: Optional[List[PhoneAdverseParty]] = None

    @field_validator("first", "middle", "last", "suffix", mode="before")
    @classmethod
    def normalize_names(cls, v):
        return normalize_to_ascii(v)

    @field_validator("middle", "suffix", "dob", "phones", mode="before")
    def falsy_to_none(cls, v):
        if not v:
            return None
        return v


class AdverseParties(RootModel[List[AdverseParty]]):
    """A list of AdverseParty objects. Can be empty if there are no adverse parties."""

    root: List[AdverseParty] = Field(default_factory=list)


######################################################################
# Asset
######################################################################


class AssetEntry(RootModel[dict[str, int]]):  # asset_name -> net present value (int)
    """
    Represents a single asset entry mapping an asset name to its integer value.
    Example: {"car": 5000}
    """

    @field_validator("root", mode="before")
    @classmethod
    def normalize_keys(cls, v):
        if isinstance(v, dict):
            return {normalize_to_ascii(k): val for k, val in v.items()}
        return v


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


class CashAssets(Assets):
    """Countable cash and bank-type assets collected in the cash-assets step."""


class InvestmentAssets(Assets):
    """Countable investments and cash-value financial assets."""


class OtherPropertyAssets(Assets):
    """Other countable non-exempt assets such as land or business property."""


######################################################################
# Caller Phone
######################################################################


class PhoneTypeCaller(str, Enum):
    """Phone types for caller/contact - uses {type}_phone field naming."""

    WORK = "work"
    OTHER = "other"
    HOME = "home"
    MOBILE = "mobile"
    FAX = "fax"


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
    suffix: Optional[str] = None
    type: NameTypeValue = NameTypeValue.FORMER_NAME

    model_config = ConfigDict(use_enum_values=True)

    @field_validator("first", "middle", "last", "suffix", mode="before")
    @classmethod
    def normalize_names(cls, v):
        return normalize_to_ascii(v)

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

    @field_validator("suffix", mode="before")
    def strip_and_normalize_suffix(cls, v):
        if isinstance(v, str):
            v = v.strip() or None
        if not v:
            return None
        return v


class CallerNames(RootModel[List[CallerName]]):
    """A list of CallerName objects."""

    @field_validator("root", mode="after")
    @classmethod
    def deduplicate_names(cls, names: List[CallerName]) -> List[CallerName]:
        """Silently deduplicate names - keep first occurrence of each unique name."""
        seen = set()
        deduplicated = []
        for name in names:
            # Create a tuple of all fields for comparison
            name_tuple = (name.first, name.middle, name.last, name.suffix)
            if name_tuple not in seen:
                seen.add(name_tuple)
                deduplicated.append(name)
        return deduplicated


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

    @field_validator("period", mode="before")
    @classmethod
    def normalize_period_aliases(cls, v):
        """Normalize common period aliases to LegalServer enum values.

        The LLM (or STT) sometimes produces values like "month" instead of "Monthly".
        This keeps the function-call path resilient and avoids user-facing correction chatter.
        """
        if v is None or isinstance(v, IncomePeriod):
            return v

        if isinstance(v, (int, float)):
            v_int = int(v)
            numeric_map = {
                1: IncomePeriod.ANNUALLY,
                4: IncomePeriod.QUARTERLY,
                12: IncomePeriod.MONTHLY,
                24: IncomePeriod.SEMI_MONTHLY,
                26: IncomePeriod.BIWEEKLY,
                52: IncomePeriod.WEEKLY,
            }
            return numeric_map.get(v_int, v)

        if not isinstance(v, str):
            return v

        raw = v.strip()
        if raw.isdigit():
            return cls.normalize_period_aliases(int(raw))

        normalized = (
            raw.lower()
            .replace("_", " ")
            .replace("-", " ")
            .replace("/", " ")
            .replace("per ", "")
            .strip()
        )

        # Common canonicalizations
        if normalized in {
            "annual",
            "annually",
            "year",
            "yearly",
            "a year",
            "yr",
            "yrs",
        }:
            return IncomePeriod.ANNUALLY
        if normalized in {"month", "monthly", "a month", "mo", "mos"}:
            return IncomePeriod.MONTHLY
        if normalized in {"week", "weekly", "a week", "wk", "wks"}:
            return IncomePeriod.WEEKLY
        if normalized in {
            "biweekly",
            "bi weekly",
            "every two weeks",
            "two weeks",
            "fortnight",
            "fortnightly",
        }:
            return IncomePeriod.BIWEEKLY
        if normalized in {"semi monthly", "semimonthly", "twice a month"}:
            return IncomePeriod.SEMI_MONTHLY
        if normalized in {"quarter", "quarterly", "a quarter"}:
            return IncomePeriod.QUARTERLY

        return raw

    @field_validator("period", mode="after")
    @classmethod
    def normalize_zero_income_period(cls, v, info):
        """When income amount is 0, normalize period to 'Monthly' for consistency."""
        if info.data.get("amount") == 0:
            return IncomePeriod.MONTHLY
        return v


class MemberIncome(
    RootModel[dict[str, IncomeDetail]]
):  # income_category_name -> IncomeDetail
    @model_validator(mode="after")
    def collapse_all_zero_income(self) -> "MemberIncome":
        """Collapse an all-zero income listing to a single 'No Household Income' entry.

        The LLM sometimes responds to "what income does this member have?" by
        exhaustively listing every income category at $0 — e.g.
        {"wages": 0, "social_security": 0, "disability": 0, ...} — rather than
        using the canonical shorthand {"No Household Income": 0}.  Sending those
        redundant entries downstream would create unnecessary LegalServer income
        records and add noise to the pipeline state.

        Post-condition: if all amounts were 0, self.root contains exactly one
        entry: {"No Household Income": IncomeDetail(amount=0, period=Monthly)}.

        Note: the household-level strip_zero_only_members validator relies on
        this invariant — it recognises a member with no real income by checking
        for the 'No Household Income' key.
        """
        if not self.root:
            return self
        if all(detail.amount == 0 for detail in self.root.values()):
            self.root = {
                "No Household Income": IncomeDetail(
                    amount=0, period=IncomePeriod.MONTHLY
                )
            }
        return self


class HouseholdIncome(
    RootModel[dict[str, MemberIncome]]
):  # person_name -> MemberIncome
    @field_validator("root", mode="before")
    @classmethod
    def normalize_keys(cls, v):
        if isinstance(v, dict):
            return {normalize_to_ascii(k): val for k, val in v.items()}
        return v

    @field_validator("root", mode="after")
    @classmethod
    def ensure_nonempty_listing(cls, v: dict[str, MemberIncome]):
        """Ensure income listing is never completely empty.

        The LLM sometimes submits `{}` for income listing to represent "no income".
        Downstream, an empty listing causes us to skip sending any income records to LegalServer.
        To make "no income" explicit, normalize an empty listing to a single
        "No Household Income" entry with amount=0 and period=Monthly.
        """
        if not v:
            default_member_income = MemberIncome.model_validate(
                {
                    "No Household Income": {
                        "amount": 0,
                        "period": IncomePeriod.MONTHLY,
                    }
                }
            )
            return {"Household": default_member_income}
        return v

    @model_validator(mode="after")
    def strip_zero_only_members(self) -> "HouseholdIncome":
        """Remove members whose only entry is 'No Household Income' at $0
        when other members exist.  The LLM sometimes lists children separately
        with no income; these redundant entries create noise in the state and
        generate unnecessary LegalServer API calls."""
        if len(self.root) <= 1:
            return self
        filtered = {
            name: member
            for name, member in self.root.items()
            if not (
                len(member.root) == 1
                and "No Household Income" in member.root
                and member.root["No Household Income"].amount == 0
            )
        }
        # Keep at least one member — if every member was zero-only, keep them all
        if filtered:
            self.root = filtered
        return self
