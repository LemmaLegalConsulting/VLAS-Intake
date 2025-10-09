import csv

import httpx
import phonenumbers
from rapidfuzz import fuzz, process, utils

from intake_bot.env_var import require_env_var
from intake_bot.globals import ROOT_DIR
from intake_bot.intake_arg_models import (
    Assets,
    ClassificationResponse,
    HouseholdIncome,
    IncomePeriod,
)
from intake_bot.poverty import poverty_scale_income_qualifies


class IntakeValidator:
    """
    Provides a set of asynchronous validation methods for intake screening.
    """

    def __init__(self, **kwargs):
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
        self.case_type_taxonomy = self._load_case_type_taxonomy(**kwargs)

    def _load_case_type_taxonomy(
        self, taxonomy_file_path: str = f"""{ROOT_DIR}/data/fetch_taxonomy.csv"""
    ) -> dict:
        with open(taxonomy_file_path, newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            taxonomy = {row["label"]: row for row in reader}
            return taxonomy

    async def check_phone_number(self, phone_number: str) -> tuple[bool, str]:
        try:
            parsed = phonenumbers.parse(phone_number, "US")
            valid = (
                phonenumbers.is_valid_number(parsed)
                and phonenumbers.region_code_for_number(parsed) == "US"
            )
            if valid:
                phone_number = phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.NATIONAL
                )
        except phonenumbers.phonenumberutil.NumberParseException:
            valid = False
        return valid, phone_number

    async def check_service_area(self, location: str) -> str:
        """
        Check if the caller's location or legal problem occurred in an eligible service area based on the city or county name.
        """
        match = process.extractOne(
            location,
            self.service_area_names,
            scorer=fuzz.WRatio,
            score_cutoff=50,
            processor=utils.default_process,
        )
        if match:
            return match[0]
        else:
            return ""

    async def check_case_type(self, case_description: str) -> dict:
        """
        Check if the caller's legal problem is a type of case that we can handle.
        """
        headers = {"Authorization": f"Bearer {require_env_var('FETCH_API_KEY')}"}
        payload = {
            "problem_description": case_description,
            "include_debug_details": False,
            "decision_mode": "vote",
        }
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    require_env_var("FETCH_URL"), headers=headers, json=payload
                )
            response.raise_for_status()
            response_validated = ClassificationResponse.model_validate(response.json())
        except Exception as e:
            raise RuntimeError(f"Error during classification: {e}")

        for label in response_validated.labels:
            if label.label in self.case_type_taxonomy.keys():
                label.legal_problem_code = self.case_type_taxonomy[label.label][
                    "legal_problem_code"
                ]
            else:
                label.legal_problem_code = ""
        return response_validated.model_dump(exclude_none=True)

    async def check_conflict_of_interest(self, opposing_party_members: list[str]) -> bool:
        """
        Check for conflict of interest with the caller's case.
        """
        if "Jimmy Dean" in opposing_party_members:
            return True
        else:
            return False

    async def check_income(self, income: HouseholdIncome) -> tuple[bool, int]:
        """
        Check the caller's income eligibility.
        """
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
        total_members = len(income.root.keys())
        is_eligible = poverty_scale_income_qualifies(
            total_monthly_income=total_monthly, household_size=total_members, multiplier=3.0
        )
        return is_eligible, total_monthly

    async def check_assets(self, assets: Assets) -> tuple[bool, int]:
        """
        Check the caller's assets eligibility.

        Args:
            assets (Assets): Pydantic RootModel wrapping a list of AssetEntry (each a dict[str,int])

        Returns:
            (is_eligible, assets_value)
        """
        vlas_assets_limit: int = 10_000

        # Each item in assets.root is an AssetEntry (RootModel[dict[str,int]])
        total_value = 0
        for asset_entry in assets.root:
            # asset_entry.root is the underlying dict[str, int]; sum its values
            for value in asset_entry.root.values():
                total_value += int(value)

        assets_value: int = int(total_value)
        is_eligible: bool = vlas_assets_limit >= assets_value

        return is_eligible, assets_value

    async def get_alternative_providers(self) -> list[str]:
        """
        Alternative legal providers for the caller.
        """
        alternatives = [
            "Center for Legal Help",
            "Local Legal Help",
        ]
        return alternatives


def check_case_type(case_description: str) -> dict:
    import asyncio

    async def _check_case_type_async(case_description: str) -> dict:
        validator = IntakeValidator()
        return await validator.check_case_type(case_description)

    return asyncio.run(_check_case_type_async(case_description))
