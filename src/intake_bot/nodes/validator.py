from pathlib import Path

import yaml
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
        self.service_areas_fips = self._load_service_areas_fips()
        self.income_categories = self._load_income_categories()
        self.classifier = Classifier()

    def _load_service_areas(self) -> list[str] | None:
        """Load service area names from YAML file."""
        service_areas_yaml_file = Path(DATA_DIR) / "service_areas.yaml"
        try:
            if not service_areas_yaml_file.exists():
                logger.warning(f"""Service Areas YAML file not found: {service_areas_yaml_file}""")
                return None

            with open(service_areas_yaml_file, "r") as f:
                data = yaml.safe_load(f)

            if not data or "service_areas" not in data:
                logger.warning("Service Areas YAML file is empty or missing 'service_areas' key")
                return None

            # Extract service area names
            names = [area.get("name") for area in data["service_areas"] if area.get("name")]
            return names if names else None
        except Exception as e:
            logger.error(
                f"""Error loading Service Areas YAML file {service_areas_yaml_file}: {e}"""
            )
            return None

    def _load_service_areas_fips(self) -> dict[str, int] | None:
        """Load service areas FIPS codes from YAML file."""
        service_areas_yaml_file = Path(DATA_DIR) / "service_areas.yaml"
        try:
            if not service_areas_yaml_file.exists():
                logger.warning(f"""Service Areas YAML file not found: {service_areas_yaml_file}""")
                return None

            with open(service_areas_yaml_file, "r") as f:
                data = yaml.safe_load(f)

            if not data or "service_areas" not in data:
                logger.warning("Service Areas YAML file is empty or missing 'service_areas' key")
                return None

            # Build mapping of service area name to FIPS code
            fips_map = {}
            for area in data["service_areas"]:
                name = area.get("name")
                fips = area.get("fips")
                if name:
                    fips_map[name] = fips

            return fips_map if fips_map else None
        except Exception as e:
            logger.error(
                f"""Error loading Service Areas YAML file {service_areas_yaml_file}: {e}"""
            )
            return None

    def _load_income_categories(self) -> list[dict] | None:
        """Load income categories from YAML file."""
        income_categories_yaml_file = Path(DATA_DIR) / "income_categories.yaml"
        try:
            if not income_categories_yaml_file.exists():
                logger.warning(
                    f"""Income Categories YAML file not found: {income_categories_yaml_file}"""
                )
                return None

            with open(income_categories_yaml_file, "r") as f:
                data = yaml.safe_load(f)

            if not data or "income_categories" not in data:
                logger.warning(
                    "Income Categories YAML file is empty or missing 'income_categories' key"
                )
                return None

            # Return the list of income categories with id and text
            categories = data["income_categories"]
            return categories if categories else None
        except Exception as e:
            logger.error(
                f"""Error loading Income Categories YAML file {income_categories_yaml_file}: {e}"""
            )
            return None

    async def check_phone_number(self, phone_number: str) -> tuple[bool, str]:
        valid, phone_number = phone_number_is_valid(phone_number=phone_number)
        return valid, phone_number

    async def check_service_area(self, location: str) -> tuple[str, int]:
        """
        Check if the caller's location or legal problem occurred in an eligible service area based on the city or county name.

        Returns:
            tuple[str, int]: (matched_location, fips_code) where fips_code is 0 if no match found
        """
        if self.service_areas is None:
            return "", 0

        match = process.extractOne(
            location,
            self.service_areas,
            scorer=fuzz.WRatio,
            score_cutoff=50,
            processor=utils.default_process,
        )

        if match:
            matched_location = match[0]
            fips_code = (
                self.service_areas_fips.get(matched_location, 0) if self.service_areas_fips else 0
            )
            return matched_location, fips_code
        else:
            return "", 0

    def get_fips_code(self, location: str) -> int | None:
        """Get the FIPS code for a service area location."""
        if self.service_areas_fips is None:
            return None
        return self.service_areas_fips.get(location)

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
