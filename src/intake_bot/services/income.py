"""Shared income arithmetic for screening and its explanation."""
from intake_bot.models.validator import HouseholdIncome, IncomePeriod

PERIODS_PER_YEAR = {
    IncomePeriod.ANNUALLY: 1, IncomePeriod.QUARTERLY: 4,
    IncomePeriod.MONTHLY: 12, IncomePeriod.SEMI_MONTHLY: 24,
    IncomePeriod.BIWEEKLY: 26, IncomePeriod.WEEKLY: 52,
}


def monthly_income_rows(income: HouseholdIncome) -> list[dict]:
    return [
        {"member": member, "source": source, "amount": detail.amount,
         "period": detail.period.value,
         "periods_per_year": PERIODS_PER_YEAR[detail.period],
         "monthly": detail.amount * PERIODS_PER_YEAR[detail.period] / 12}
        for member, sources in income.root.items()
        for source, detail in sources.root.items()
    ]
