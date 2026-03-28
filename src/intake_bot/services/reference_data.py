import re
from pathlib import Path
from typing import Dict, Optional

import yaml
from intake_bot.utils.globals import DATA_DIR
from loguru import logger


class ReferenceDataLoader:
    """
    Singleton-like loader for reference data used throughout the intake system.
    Loads once and caches all data to avoid repeated file I/O.
    """

    _instance = None
    _data: Optional[Dict] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if ReferenceDataLoader._data is None:
            self._load_data()

    @classmethod
    def _load_data(cls):
        """Load all reference data from consolidated YAML file."""
        ref_file = Path(DATA_DIR) / "reference_data.yml"
        try:
            if not ref_file.exists():
                raise FileNotFoundError(
                    f"""Reference data file not found: {ref_file}"""
                )
            with open(ref_file) as f:
                ReferenceDataLoader._data = yaml.safe_load(f)
            logger.debug(f"""Loaded reference data from {ref_file}""")
        except Exception as e:
            logger.error(f"""Error loading reference data from {ref_file}: {e}""")
            ReferenceDataLoader._data = {}

    @staticmethod
    def _normalize_service_area_text(location: str) -> str:
        return re.sub(r"""\s+""", " ", location.strip().lower())

    @classmethod
    def _build_service_area_aliases(
        cls, service_areas: Dict[str, int]
    ) -> Dict[str, tuple[str, int]]:
        aliases: Dict[str, tuple[str, int]] = {}

        for canonical_name, fips_code in service_areas.items():
            normalized_name = cls._normalize_service_area_text(canonical_name)
            aliases.setdefault(normalized_name, (canonical_name, fips_code))

            for suffix in (" city", " county"):
                if normalized_name.endswith(suffix):
                    alias = normalized_name[: -len(suffix)].strip()
                    if alias:
                        aliases.setdefault(alias, (canonical_name, fips_code))

        return aliases

    @property
    def service_areas(self) -> Dict[str, int]:
        """Get service areas mapping (county/city name to FIPS code)."""
        return ReferenceDataLoader._data.get("service_areas", {})

    @property
    def service_area_aliases(self) -> Dict[str, tuple[str, int]]:
        """Get normalized service-area aliases mapped to canonical names and FIPS codes."""
        return self._build_service_area_aliases(self.service_areas)

    @property
    def income_categories(self) -> list[str]:
        """Get income categories as a list of category names."""
        return ReferenceDataLoader._data.get("income_categories", [])

    @property
    def legal_problem_codes(self) -> Dict[str, str]:
        """
        Get legal problem codes as dictionary.

        Returns mapping where:
        - key: normalized label without code (e.g., "Private Landlord/Tenant")
        - value: full original entry (e.g., "63 Private Landlord/Tenant")
        """
        return ReferenceDataLoader._data.get("legal_problem_codes", {})

    def get_all(self) -> Dict:
        """Get all loaded reference data."""
        return ReferenceDataLoader._data or {}
