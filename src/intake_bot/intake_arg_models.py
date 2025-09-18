from enum import Enum

from pydantic import BaseModel, Field, RootModel


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
