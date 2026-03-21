import re
from datetime import datetime

from intake_bot.models.classifier import ClassificationResponse
from intake_bot.models.validator import (
    Assets,
    HouseholdIncome,
    IncomePeriod,
)
from intake_bot.services.classifier import Classifier
from intake_bot.services.phonenumber import phone_number_is_valid
from intake_bot.services.poverty import poverty_scale_income_qualifies
from intake_bot.services.reference_data import ReferenceDataLoader
from rapidfuzz import fuzz, process, utils


class IntakeValidator:
    """
    Provides a set of asynchronous validation methods for intake screening.
    """

    def __init__(self):
        self.service_areas = ReferenceDataLoader().service_areas
        self.classifier = Classifier()

    async def check_phone_number(self, phone_number: str) -> tuple[bool, str]:
        """
        Validate a phone number and return its validity status and normalized format.

        Args:
            phone_number (str): The phone number string to validate.

        Returns:
            tuple[bool, str]: A tuple containing:
                - bool: True if the phone number is valid, False otherwise.
                - str: The normalized/formatted phone number if valid, or the original input if invalid.
        """
        valid, phone_number = phone_number_is_valid(phone_number=phone_number)
        return valid, phone_number

    async def check_date_of_birth(self, dob_string: str) -> tuple[bool, str]:
        """
        Validate a date of birth and return its validity status and ISO format (YYYY-MM-DD).

        Args:
            dob_string (str): The date of birth string to validate. Ideally in ISO format (YYYY-MM-DD),
                            but the validator attempts to parse various common formats.

        Returns:
            tuple[bool, str]: A tuple containing:
                - bool: True if the date of birth is valid and in the past, False otherwise.
                - str: The ISO formatted date (YYYY-MM-DD) if valid, or empty string if invalid.
        """
        cleaned = self._normalize_spanish_months(dob_string.strip())

        # Common date formats
        formats = [
            "%m/%d/%Y",
            "%m-%d-%Y",
            "%Y-%m-%d",
            "%B %d, %Y",
            "%b %d, %Y",
            "%m/%d/%y",
            "%m-%d-%y",
            "%d %B %Y",
            "%d %b %Y",
        ]
        for fmt in formats:
            try:
                dob = datetime.strptime(cleaned, fmt)
                if dob.date() >= datetime.now().date():
                    return False, ""
                return True, dob.strftime("%Y-%m-%d")
            except ValueError:
                continue
        return False, ""

    @staticmethod
    def _normalize_spanish_months(text: str) -> str:
        """Replace Spanish month names and strip the 'de' preposition so
        standard strptime formats can parse the result."""
        _SPANISH_MONTHS = {
            "enero": "January",
            "febrero": "February",
            "marzo": "March",
            "abril": "April",
            "mayo": "May",
            "junio": "June",
            "julio": "July",
            "agosto": "August",
            "septiembre": "September",
            "octubre": "October",
            "noviembre": "November",
            "diciembre": "December",
        }
        lowered = text.lower()
        for es, en in _SPANISH_MONTHS.items():
            if es in lowered:
                lowered = lowered.replace(es, en)
                break
        # Strip Spanish preposition "de" between date parts (e.g. "8 de July de 1980")
        lowered = re.sub(r"\bde\b", "", lowered).strip()
        lowered = re.sub(r"\s{2,}", " ", lowered)
        return lowered

    async def check_ssn_last_4(self, ssn_last_4: str) -> tuple[bool, str]:
        """
        Validate the last 4 digits of a social security number.

        Args:
            ssn_last_4 (str): The last 4 digits of SSN (accepts various formats like XXXX, XXX-X, X-XXX, X-X-XX, etc.)

        Returns:
            tuple[bool, str]: A tuple containing:
                - bool: True if valid (exactly 4 digits after removing separators), False otherwise.
                - str: The 4-digit SSN if valid, or empty string if invalid.
        """
        cleaned = ssn_last_4.strip().replace("-", "").replace(" ", "").replace("_", "")
        if len(cleaned) == 4 and cleaned.isdigit():
            return True, cleaned
        return False, ""

    async def check_service_area(self, location: str) -> tuple[str, int]:
        """
        Check if the caller's location or legal problem occurred in an eligible service area based on the city or county name.

        Returns:
            tuple[str, int]: (matched_location, fips_code) where fips_code is 0 if no match found
        """
        match = process.extractOne(
            location,
            self.service_areas.keys(),
            scorer=fuzz.WRatio,
            score_cutoff=50,
            processor=utils.default_process,
        )

        if match:
            matched_location = match[0]
            fips_code = self.service_areas.get(matched_location, 0)
            return matched_location, fips_code
        else:
            return "", 0

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

    async def check_income(
        self, income: HouseholdIncome, household_size: int | None = None
    ) -> tuple[bool, int, int]:
        """
        Check the caller's income eligibility.
        """
        total_monthly_income = 0.0
        for member_income in income.root.values():
            for income_detail in member_income.root.values():
                amt = income_detail.amount
                period = income_detail.period
                if period == IncomePeriod.ANNUALLY:
                    total_monthly_income += amt / 12
                elif period == IncomePeriod.MONTHLY:
                    total_monthly_income += amt
                elif period == IncomePeriod.WEEKLY:
                    total_monthly_income += (amt * 52) / 12
                elif period == IncomePeriod.BIWEEKLY:
                    total_monthly_income += (amt * 26) / 12
                elif period == IncomePeriod.SEMI_MONTHLY:
                    total_monthly_income += amt * 2
                elif period == IncomePeriod.QUARTERLY:
                    total_monthly_income += (amt * 4) / 12
                else:
                    raise ValueError(f"""Unknown period: {period}""")
        total_monthly_income = int(total_monthly_income)
        if household_size is None or household_size < 1:
            household_size = max(len(income.root.keys()), 1)
        is_eligible = poverty_scale_income_qualifies(
            total_monthly_income=total_monthly_income,
            household_size=household_size,
            multiplier=3.0,
        )
        return is_eligible, total_monthly_income, household_size

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

    async def check_household_composition(
        self, adults: int, children: int
    ) -> tuple[bool, int]:
        """
        Validate household composition numbers.

        Args:
            adults (int): Number of adults in the household (excluding anyone who has perpetrated domestic violence against you)
            children (int): Number of children in the household (under 18)

        Returns:
            tuple[bool, int]: (is_valid, total_household_size)
        """
        if not isinstance(adults, int) or not isinstance(children, int):
            return False, 0

        if adults < 1:
            return False, 0

        if children < 0:
            return False, 0

        total_size = adults + children

        if total_size > 25:  # Sanity check for unreasonable values
            return False, 0

        return True, total_size
