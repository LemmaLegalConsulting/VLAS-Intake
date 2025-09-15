import asyncio

import phonenumbers
from rapidfuzz import fuzz, process, utils

from intake_bot.intake_arg_models import HouseholdIncome, IncomePeriod


class MockRemoteSystem:
    """Simulates a remote system API."""

    def __init__(self):
        self.service_area_names = [
            "Amelia County",
            "Amherst County",
            "Appomattox County",
            "Bedford County",
            "Brunswick County",
            "Campbell County",
            "Danville City",
            "Emporia City",
            "Franklin City",
            "Greensville County",
            "Halifax County",
            "Henry County",
            "Isle of Wight County",
            "Lunenburg County",
            "Lynchburg City",
            "Martinsville City",
            "Mecklenburg County",
            "Nottoway County",
            "Patrick County",
            "Pittsylvania County",
            "Prince Edward County",
            "South Boston",
            "Southampton County",
            "Suffolk City",
            "Sussex County",
        ]
        # case_type: conflict_check
        self.case_types = {
            "bankruptcy": {
                "conflict_check": True,
                "domestic_violence": "yes",
            },
            "citation": {
                "conflict_check": False,
                "domestic_violence": "no",
            },
            "divorce": {
                "conflict_check": True,
                "domestic_violence": "ask",
            },
            "domestic violence": {
                "conflict_check": True,
                "domestic_violence": "yes",
            },
        }

    async def valid_phone_number(self, phone: str) -> tuple[bool, str]:
        try:
            phone_number = phonenumbers.parse(phone, "US")
            valid = phonenumbers.is_valid_number(phone_number)
            if valid:
                phone = phonenumbers.format_number(phone_number, phonenumbers.PhoneNumberFormat.NATIONAL)
        except phonenumbers.phonenumberutil.NumberParseException:
            valid = False
        return valid, phone

    async def get_alternative_providers(self) -> list[str]:
        """Alternative legal providers for the caller."""
        alternatives = [
            "Center for Legal Help",
            "Local Legal Help",
        ]
        return alternatives

    async def check_case_type(self, case_type: str) -> tuple[bool, bool, bool]:
        """Check if the caller's legal problem is a type of case that we can handle."""

        # Simulate API call delay
        await asyncio.sleep(0.5)

        is_eligible: bool = str(case_type).strip().lower() in self.case_types
        conflict_check_required: bool = self.case_types.get(case_type, {}).get("conflict_check", False)
        domestic_violence: bool = self.case_types.get(case_type, {}).get("domestic_violence", False)
        return is_eligible, conflict_check_required, domestic_violence

    async def check_service_area(self, caller_area: str) -> str:
        """Check if the caller's location or legal problem occurred in an eligible service area based on the city or county name."""

        match = process.extractOne(
            caller_area, self.service_area_names, scorer=fuzz.WRatio, score_cutoff=50, processor=utils.default_process
        )
        if match:
            return match[0]
        else:
            return ""

    async def check_conflict_of_interest(self, opposing_party_members: list[str]) -> bool:
        """Check for conflict of interest with the caller's case."""
        if "Jimmy Dean" in opposing_party_members:
            return True
        else:
            return False

    async def check_income(self, income: HouseholdIncome) -> tuple[bool, int, int]:
        """Check the caller's income eligibility."""
        federal_poverty_yearly = 15650
        federal_poverty_monthly = federal_poverty_yearly / 12

        total_monthly = 0.0
        for member_income in income.root.values():
            for income_detail in member_income.root.values():
                amt = income_detail.amount
                period = income_detail.period
                if period == IncomePeriod.year:
                    total_monthly += amt / 12
                elif period == IncomePeriod.month:
                    total_monthly += amt
                else:
                    raise ValueError(f"Unknown period: {period}")

        total_monthly = int(total_monthly)
        poverty_percent = int((total_monthly / federal_poverty_monthly) * 100)
        is_eligible = poverty_percent <= 300
        return is_eligible, total_monthly, poverty_percent

    async def check_assets(self, assets: dict[str, float]) -> tuple[bool, int]:
        """Check the caller's assets eligibility."""
        vlas_assets_limit: int = 10_000

        assets_value: int = int(sum(value for asset in assets for value in asset.values()))
        is_eligible: bool = vlas_assets_limit >= assets_value

        return is_eligible, assets_value
