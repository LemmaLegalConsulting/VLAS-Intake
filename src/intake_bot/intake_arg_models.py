from enum import Enum

from pydantic import BaseModel, Field, RootModel


class IncomePeriod(str, Enum):
    month = "month"
    year = "year"


class IncomeDetail(BaseModel):
    amount: int = Field(..., description="The amount of income received.")
    period: IncomePeriod = Field(..., description='The period for the income, either "month" or "year".')


class MemberIncome(RootModel[dict[str, IncomeDetail]]):  # income_type -> IncomeDetail
    pass


class HouseholdIncome(RootModel[dict[str, MemberIncome]]):  # person_name -> MemberIncome
    pass
