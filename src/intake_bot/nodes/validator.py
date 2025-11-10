from pathlib import Path

from intake_bot.models.classifier import ClassificationResponse
from intake_bot.models.validator import (
    Assets,
    HouseholdIncome,
    IncomePeriod,
)
from intake_bot.services.classifier import Classifier
from intake_bot.services.phonenumber import phone_number_is_valid
from intake_bot.services.poverty import poverty_scale_income_qualifies
from intake_bot.utils.globals import (
    DATA_DIR,
)
from loguru import logger
from rapidfuzz import fuzz, process, utils


class IntakeValidator:
    """
    Provides a set of asynchronous validation methods for intake screening.
    """

    def __init__(self):
        self.service_areas = self._load_service_areas()
        self.classifier = Classifier()

    def _load_service_areas(self) -> list[str] | None:
        service_areas_file = Path(DATA_DIR) / "service_areas.txt"
        try:
            if not service_areas_file.exists():
                logger.warning(f"""Service Areas file not found: {service_areas_file}""")
                return None
            content = service_areas_file.read_text()
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            if not lines:
                logger.warning(
                    f"""Service Areas file {service_areas_file} contains no valid entries"""
                )
                return None
            return lines
        except Exception as e:
            logger.error(f"""Error loading Service Areas file {service_areas_file}: {e}""")
            return None

    async def check_phone_number(self, phone_number: str) -> tuple[bool, str]:
        valid, phone_number = phone_number_is_valid(phone_number=phone_number)
        return valid, phone_number

    async def check_service_area(self, location: str) -> str:
        """
        Check if the caller's location or legal problem occurred in an eligible service area based on the city or county name.
        """
        if self.service_areas is None:
            return location
        match = process.extractOne(
            location,
            self.service_areas,
            scorer=fuzz.WRatio,
            score_cutoff=50,
            processor=utils.default_process,
        )
        if match:
            return match[0]
        else:
            return ""

    async def check_case_type(self, case_description: str) -> ClassificationResponse:
        """
        Check if the caller's legal problem is a type of case that we can handle.

        Uses the local Classifier to classify the legal problem. Returns a dict with:
        - legal_problem_code: Full code string (e.g., "63 Private Landlord/Tenant")
        - confidence: 0-1 score
        - is_eligible: bool (False if code starts with "00")
        - follow_up_questions: Optional questions if confidence is below threshold
        """
        return await self.classifier.classify(problem_description=case_description)

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

        total_value = 0
        for asset_entry in assets.root:
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
