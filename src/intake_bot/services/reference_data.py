from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from intake_bot.utils.globals import DATA_DIR

LocalityInfo = dict[str, Any]


def normalize_text(text: str) -> str:
    ascii_text = (
        unicodedata.normalize("NFKD", str(text))
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    return re.sub(r"\s+", " ", ascii_text.strip().lower())


class ReferenceDataLoader:
    """
    Singleton-like loader for reference data used throughout the intake system.
    Loads once and caches all data to avoid repeated file I/O.
    """

    _instance = None
    _data: dict | None = None

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
        except Exception:  # noqa: BLE001 - unavailable reference data degrades to empty
            logger.error("Error loading reference data")
            ReferenceDataLoader._data = {}

    @property
    def virginia_localities(self) -> dict[str, LocalityInfo]:
        return ReferenceDataLoader._data.get("virginia_localities", {})

    @property
    def official_name_normalizations(self) -> dict[str, str]:
        raw = ReferenceDataLoader._data.get("official_name_normalizations", {})
        return {normalize_text(k): v for k, v in raw.items()}

    @property
    def ambiguous_names(self) -> dict[str, list[str]]:
        raw = ReferenceDataLoader._data.get("ambiguous_names", {})
        return {normalize_text(k): v for k, v in raw.items()}

    @property
    def income_categories(self) -> list[str]:
        return ReferenceDataLoader._data.get("income_categories", [])

    @property
    def legal_problem_codes(self) -> dict[str, str]:
        return ReferenceDataLoader._data.get("legal_problem_codes", {})

    def get_all(self) -> dict:
        return ReferenceDataLoader._data or {}

    @property
    def classifier_taxonomy(self) -> list[str]:
        return sorted(self.legal_problem_codes.keys())

    def label_for_legal_problem_code(self, code: str) -> str | None:
        code = code.strip()
        for label, full_entry in self.legal_problem_codes.items():
            if full_entry == code:
                return label
        for label, full_entry in self.legal_problem_codes.items():
            entry_prefix, _, _ = full_entry.partition(" ")
            if entry_prefix == code:
                return label
        return None

    def legal_problem_code_from_label(self, label: str) -> str | None:
        return self.legal_problem_codes.get(label.strip())

    @staticmethod
    def _strip_trailing_punctuation(text: str) -> str:
        return text.strip(".,!?;:")

    @staticmethod
    def _strip_va_suffix(words: list[str]) -> list[str]:
        if not words:
            return words
        if len(words) >= 3:
            suffix = " ".join(words[-3:]).strip(".,!?;:")
            if suffix == "commonwealth of virginia":
                return words[:-3]
        last = words[-1].strip(".,!?;:")
        if last in ("va", "virginia"):
            return words[:-1]
        return words

    @staticmethod
    def _is_out_of_state(normalized: str) -> bool:
        words = ReferenceDataLoader._clean_words(normalized)
        NON_VA_STATES = frozenset(
            {
                "alabama",
                "alaska",
                "arizona",
                "arkansas",
                "california",
                "colorado",
                "connecticut",
                "delaware",
                "florida",
                "georgia",
                "hawaii",
                "idaho",
                "illinois",
                "indiana",
                "iowa",
                "kansas",
                "kentucky",
                "louisiana",
                "maine",
                "maryland",
                "massachusetts",
                "michigan",
                "minnesota",
                "mississippi",
                "missouri",
                "montana",
                "nebraska",
                "nevada",
                "new hampshire",
                "new jersey",
                "new mexico",
                "new york",
                "north carolina",
                "north dakota",
                "ohio",
                "oklahoma",
                "oregon",
                "pennsylvania",
                "rhode island",
                "south carolina",
                "south dakota",
                "tennessee",
                "texas",
                "utah",
                "vermont",
                "washington state",
                "west virginia",
                "wisconsin",
                "wyoming",
                "district of columbia",
                "puerto rico",
                "guam",
                "u.s. virgin islands",
                "us virgin islands",
                "american samoa",
                "northern mariana islands",
            }
        )
        NON_VA_ABBREVS = frozenset(
            {
                "al",
                "ak",
                "az",
                "ar",
                "ca",
                "co",
                "ct",
                "de",
                "fl",
                "ga",
                "hi",
                "id",
                "il",
                "in",
                "ia",
                "ks",
                "ky",
                "la",
                "me",
                "md",
                "ma",
                "mi",
                "mn",
                "ms",
                "mo",
                "mt",
                "ne",
                "nv",
                "nh",
                "nj",
                "nm",
                "ny",
                "nc",
                "nd",
                "oh",
                "ok",
                "or",
                "pa",
                "ri",
                "sc",
                "sd",
                "tn",
                "tx",
                "ut",
                "vt",
                "wa",
                "wv",
                "wi",
                "wy",
                "dc",
                "pr",
                "gu",
                "vi",
                "as",
                "mp",
            }
        )

        for n in range(4, 0, -1):
            for i in range(len(words) - n + 1):
                phrase = " ".join(words[i : i + n])
                if phrase in NON_VA_STATES:
                    return True
        for i, word in enumerate(words):
            if word == "washington":
                if i + 1 < len(words) and words[i + 1] == "county":
                    continue
                return True
            if word in NON_VA_STATES:
                return True
        return bool(words and words[-1] in NON_VA_ABBREVS)

    @staticmethod
    def _clean_words(normalized: str) -> list[str]:
        return [w.strip(".,!?;:") for w in normalized.split()]

    def _match_anchored(self, normalized: str) -> str | None:
        """Full-input anchored matching.  Returns canonical name or None."""
        words = self._clean_words(normalized.replace(",", " "))
        words = self._strip_va_suffix(words)
        if not words:
            return None
        stripped = " ".join(words)

        # 1. Exact canonical
        for canonical in self.virginia_localities:
            if normalize_text(canonical) == stripped:
                return canonical

        # 2. Official-name normalization (not a generic bare-name alias).
        alias_match = self.official_name_normalizations.get(stripped)
        if alias_match and alias_match in self.virginia_localities:
            return alias_match

        # 3. "<name> County" / "<name> City" — anchored at end
        if len(words) >= 2:
            suffix = words[-1]
            if suffix in ("county", "city"):
                name_words = words[:-1]
                candidate = " ".join(name_words).title() + " " + suffix.title()
                for canonical in self.virginia_localities:
                    if normalize_text(canonical) == normalize_text(candidate):
                        return canonical

        # 4. "County of <name>" / "City of <name>"
        if len(words) >= 3 and words[0] in ("county", "city") and words[1] == "of":
            name_words = words[2:]
            candidate = " ".join(name_words).title() + " " + words[0].title()
            for canonical in self.virginia_localities:
                if normalize_text(canonical) == normalize_text(candidate):
                    return canonical

        return None

    def _has_anchor_term(self, normalized: str) -> bool:
        words = normalized.strip(".,!?;:").split()
        return any(w.strip(".,!?;:") in ("county", "city") for w in words)

    def resolve_service_area(self, location: str) -> dict:
        result = {
            "outcome": "unknown",
            "canonical_name": None,
            "fips": None,
            "is_eligible": None,
            "candidates": [],
            "match_type": None,
        }

        normalized = normalize_text(location)
        if not normalized:
            return result

        # 1. Full-input anchored matching. This must precede state-token checks so
        # canonical localities such as Virginia Beach are not mistaken for a state.
        anchored = self._match_anchored(normalized)
        if anchored:
            info = self.virginia_localities[anchored]
            result.update(
                outcome="exact_match",
                canonical_name=anchored,
                fips=info["fips"],
                is_eligible=info["is_eligible"],
                match_type="canonical",
            )
            return result

        # 2. Check for explicit non-Virginia states (with punctuation stripped).
        state_cleaned = self._strip_trailing_punctuation(normalized)
        if self._is_out_of_state(state_cleaned):
            result["outcome"] = "unserved"
            return result

        # 3. Check ambiguous names (only bare ambiguous names, not county/city forms)
        ambig_match = self.ambiguous_names.get(
            self._strip_trailing_punctuation(normalized)
        )
        if ambig_match and not self._has_anchor_term(normalized):
            result.update(
                outcome="ambiguous",
                fips=None,
                is_eligible=None,
                candidates=ambig_match,
                match_type="ambiguous",
            )
            return result

        # 4. If input contains county/city anchor and anchored forms didn't match, unresolved
        if self._has_anchor_term(normalized):
            result["outcome"] = "unresolved_service_area"
            return result

        # 5. Bare "Virginia" must not fuzzy-suggest anything
        bare = self._strip_trailing_punctuation(normalized)
        if bare in ("virginia", "va"):
            result["outcome"] = "unresolved_service_area"
            return result

        # 6. Bare names of covered localities are suggestions, not exact matches.
        bare_matches = [
            canonical
            for canonical in self.virginia_localities
            if normalize_text(canonical.rsplit(" ", 1)[0]) == bare
        ]
        if len(bare_matches) == 1:
            result.update(
                outcome="suggested",
                canonical_name=bare_matches[0],
                fips=None,
                is_eligible=None,
                candidates=bare_matches,
                match_type="bare_name",
            )
            return result

        # 7. Fuzzy match — rank suggestions only
        from rapidfuzz import fuzz, process, utils

        locality_names = list(self.virginia_localities.keys())
        fuzzy_matches = process.extract(
            location,
            locality_names,
            scorer=fuzz.WRatio,
            score_cutoff=50,
            limit=5,
            processor=utils.default_process,
        )

        if not fuzzy_matches:
            result["outcome"] = "unresolved_service_area"
            return result

        strong_matches = [
            (name, score) for name, score, _ in fuzzy_matches if score >= 75
        ]

        if len(strong_matches) == 1:
            name, _score = strong_matches[0]
            info = self.virginia_localities[name]
            result.update(
                outcome="suggested",
                canonical_name=name,
                fips=None,
                is_eligible=None,
                candidates=[name],
                match_type="fuzzy",
            )
        elif len(strong_matches) == 2:
            candidates = [name for name, _ in strong_matches]
            result.update(
                outcome="ambiguous",
                fips=None,
                is_eligible=None,
                candidates=candidates,
                match_type="fuzzy",
            )
        elif len(strong_matches) > 2:
            candidates = [name for name, _ in strong_matches[:2]]
            result.update(
                outcome="ambiguous",
                fips=None,
                is_eligible=None,
                candidates=candidates,
                match_type="fuzzy",
            )
        else:
            result.update(
                outcome="unresolved_service_area",
                fips=None,
                is_eligible=None,
                candidates=[],
                match_type="fuzzy",
            )

        return result
