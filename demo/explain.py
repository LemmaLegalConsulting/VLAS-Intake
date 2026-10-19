"""Public demo fixtures and explanations derived from the actual intake state."""
import re
from functools import lru_cache

from intake_bot.models.validator import HouseholdIncome
from intake_bot.services.income import monthly_income_rows
from intake_bot.services.poverty import get_poverty_scale_data, poverty_scale_get_income_limit
from intake_bot.services.reference_data import ReferenceDataLoader


FICTIONAL_CLIENTS = [
    {"name": "Jordan Vale", "record": "DEMO-1042", "matter": "Landlord advice — fictional existing client"},
    {"name": "Morgan Reed", "record": "DEMO-2091", "matter": "Housing advice — fictional existing client"},
]


def normalize_name(name):
    return re.sub(r"[^\w]+", " ", name.casefold()).strip()


def conflict_explanation(state):
    collected = state.get("adverse_parties")
    if collected is None:
        return {"status": "waiting", "matches": [], "searched": []}
    names = [" ".join(str(p.get(k) or "") for k in ("first", "middle", "last", "suffix")).strip()
             for p in collected.get("adverse_parties", [])]
    matches = [{**record, "searched_name": name} for name in names
               for record in FICTIONAL_CLIENTS if normalize_name(name) == normalize_name(record["name"])]
    return {"status": "possible_match" if matches else "no_demo_match",
            "matches": matches, "searched": names}


def income_explanation(state):
    income = state.get("income")
    if not income or "listing" not in income:
        return None
    rows = monthly_income_rows(HouseholdIncome.model_validate(income["listing"]))
    household_size = income["household_size"]
    annual_limit = poverty_scale_get_income_limit(household_size, multiplier=3.0)
    scale = get_poverty_scale_data()
    return {"rows": rows, "total": income["monthly_amount"],
            "household_size": household_size, "annual_limit": annual_limit,
            "monthly_limit": round(annual_limit / 12), "multiplier": 3,
            "base": scale["poverty_base"], "increment": scale["poverty_increment"],
            "year": scale["poverty_level_update_year"], "within_demo_limit": income["is_eligible"]}


def explanations(state):
    classification = state.get("_demo_classification")
    if not classification and state.get("case_type"):
        case = state["case_type"]
        classification = {"status": "matched", "description": case.get("case_description"),
                          "code": case.get("legal_problem_code"), "votes": [], "questions": []}
    return {"classification": classification, "income": income_explanation(state),
            "conflict": conflict_explanation(state)}


@lru_cache(maxsize=1)
def catalog():
    reference = ReferenceDataLoader()
    income = {"Alex Parker": {"Employment": {"amount": 600, "period": "Weekly"}},
              "Sam Parker": {"Employment": {"amount": 300, "period": "Semi-Monthly"}}}
    rows = monthly_income_rows(HouseholdIncome.model_validate(income))
    monthly = int(sum(row["monthly"] for row in rows))
    sample = {
        "_demo_classification": {
            "status": "example", "description": "My private landlord, Jordan Vale, is trying to evict me for unpaid rent. I live in Campbell County. This is not subsidized housing.",
            "code": reference.legal_problem_codes["Private Landlord/Tenant"],
            "votes": [], "questions": [],
        },
        "income": {"listing": income, "household_size": 3, "monthly_amount": monthly,
                   "is_eligible": monthly <= round(poverty_scale_get_income_limit(3, 3) / 12)},
        "adverse_parties": {"adverse_parties": [{"first": "Jordan", "last": "Vale"}]},
    }
    localities = [name for name, data in reference.virginia_localities.items() if data.get("is_eligible")]
    return {"counties": [name for name in localities if name.endswith("County")],
            "cities": [name for name in localities if name.endswith("City")],
            "fictional_clients": FICTIONAL_CLIENTS, "example": explanations(sample)}
