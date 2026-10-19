from intake_bot.models.classifier import ProviderResult, ProviderStatus, ProviderLabel, ProviderQuestion

import pytest
from demo.explain import catalog, conflict_explanation, explanations, income_explanation
from intake_bot.models.validator import HouseholdIncome
from intake_bot.services.classifier import Classifier
from intake_bot.services.income import monthly_income_rows


def test_sample_is_explicitly_fictional_and_matches_repository_rules():
    demo = catalog()
    assert "Campbell County" in demo["counties"]
    assert "Lynchburg City" in demo["cities"]
    example = demo["example"]
    assert example["classification"]["status"] == "example"
    assert example["classification"]["code"] == "63 Private Landlord/Tenant"
    assert example["income"]["total"] == 3200
    assert example["income"]["monthly_limit"] == 6830
    assert example["conflict"]["matches"][0]["record"] == "DEMO-1042"
    assert explanations({}) == {"classification": None, "income": None,
                                "conflict": {"status": "waiting", "matches": [], "searched": []}}


@pytest.mark.parametrize("period,expected", [("Annually", 100), ("Quarterly", 400),
    ("Monthly", 1200), ("Semi-Monthly", 2400), ("Biweekly", 2600), ("Weekly", 5200)])
def test_income_conversions(period, expected):
    income = HouseholdIncome.model_validate({"Sample": {"Employment": {"amount": 1200, "period": period}}})
    assert monthly_income_rows(income)[0]["monthly"] == expected


def test_conflict_match_is_full_name_only_and_does_not_expose_party_identifiers():
    state = {"adverse_parties": {"adverse_parties": [{"first": "JORDAN", "last": "Vale.", "dob": "1900-01-01"}]}}
    result = conflict_explanation(state)
    assert result["status"] == "possible_match"
    assert "1900-01-01" not in str(result)
    state["adverse_parties"]["adverse_parties"][0]["last"] = "Other"
    assert conflict_explanation(state)["status"] == "no_demo_match"


def test_income_explanation_preserves_recorded_total_and_all_household_members():
    state = {"income": {"listing": {"Sample": {"wages": {"amount": 101, "period": "Weekly"}}},
                        "household_size": 4, "monthly_amount": 437, "is_eligible": True}}
    detail = income_explanation(state)
    assert detail["total"] == 437
    assert detail["rows"][0]["monthly"] == pytest.approx(437.6666667)
    assert detail["household_size"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("confidence,needs_details", [(.95, False), (.4, True)])
async def test_classification_explanation_preserves_votes_and_followups(confidence, needs_details):
    classifier = Classifier.__new__(Classifier)
    classifier.model_weights = {"gpt-4.1-mini": 1}
    classifier.follow_up_threshold = .7
    response = await classifier._get_voted_results([
        ProviderResult(model_name="gpt-4.1-mini", status=ProviderStatus.SUCCESS,
            labels=[ProviderLabel(legal_problem_code="Housing", confidence=confidence)],
            questions=[ProviderQuestion(question="Is the housing subsidized?")])
    ], {"Housing": "63 Private Landlord/Tenant"})
    assert response.legal_problem_code == "63 Private Landlord/Tenant"
    assert bool(response.follow_up_questions) == needs_details
    if needs_details:
        assert response.follow_up_questions[0].question == "Is the housing subsidized?"
    assert response.provider_votes == [{"provider": "gpt-4.1-mini",
        "code": "63 Private Landlord/Tenant", "confidence": confidence, "weight": 1}]
