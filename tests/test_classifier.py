import pytest
from intake_bot.services.classifier import Classifier

# ================================================================
# Configuration - Toggle which models to test here
# ================================================================
# Uncomment the models you want to test
ENABLED_MODELS = [
    # "gpt-4o-mini",
    # "gpt-4.1-mini",
    # "gemini-2.5-flash",
    # "gpt-5-nano",
    "keyword",
]


# ================================================================
# Test Cases - Covers all major LSC legal problem codes
# ================================================================
TEST_CASES = [
    # 00 Criminal Defense
    {
        "problem_description": "I need criminal defense.",
        "expected_code": "00 Criminal Defense",
        "case_name": "criminal",
    },
    # 01 Bankruptcy/Debtor Relief
    {
        "problem_description": "I need to declare bankruptcy.",
        "expected_code": "01 Bankruptcy/Debtor Relief",
        "case_name": "bankruptcy",
    },
    # # 02 Collect/Repo/Def/Garnsh
    # {
    #     "problem_description": "My wages are being garnished.",
    #     "expected_code": "02 Collect/Repo/Def/Garnsh",
    #     "case_name": "debt_collection",
    # },
    # # 03 Contract/Warranties
    # {
    #     "problem_description": "I bought something that broke and the seller won't honor the warranty.",
    #     "expected_code": "03 Contract/Warranties",
    #     "case_name": "contract_warranty",
    # },
    # # 04 Collection Practices / Creditor Harassment
    # {
    #     "problem_description": "A creditor is calling me multiple times daily with threats.",
    #     "expected_code": "04 Collection Practices / Creditor Harassment",
    #     "case_name": "creditor_harassment",
    # },
    # # 05 Predatory Lending Practices (Not Mortgages)
    # {
    #     "problem_description": "I took out a payday loan with 400% interest.",
    #     "expected_code": "05 Predatory Lending Practices (Not Mortgages)",
    #     "case_name": "predatory_lending",
    # },
    # # 06 Loans/Installment Purchases (Not Collections)
    # {
    #     "problem_description": "The payment for my car loan seems wrong.",
    #     "expected_code": "06 Loans/Installment Purchases (Not Collections)",
    #     "case_name": "auto_loan",
    # },
    # # 07 Public Utilities
    # {
    #     "problem_description": "The electric company turned off my power without notice.",
    #     "expected_code": "07 Public Utilities",
    #     "case_name": "utilities",
    # },
    # # 08 Unfair and Deceptive Sales Practices (Not Real Property)
    # {
    #     "problem_description": "A store advertised zero-down but charged me hidden fees.",
    #     "expected_code": "08 Unfair and Deceptive Sales Practices (Not Real Property)",
    #     "case_name": "deceptive_sales",
    # },
    # # 12 Discipline (Including Expulsion and Suspension)
    # {
    #     "problem_description": "My son was expelled from school without a hearing.",
    #     "expected_code": "12 Discipline (Including Expulsion and Suspension)",
    #     "case_name": "school_expulsion",
    # },
    # # 13 Special Education/Learning Disabilities
    # {
    #     "problem_description": "The school won't provide an IEP for my daughter with dyslexia.",
    #     "expected_code": "13 Special Education/Learning Disabilities",
    #     "case_name": "special_education",
    # },
    # # 21 Employment Discrimination
    # {
    #     "problem_description": "I was fired after I disclosed I'm pregnant.",
    #     "expected_code": "21 Employment Discrimination",
    #     "case_name": "employment_discrimination",
    # },
    # # 22 Wage Claims and Other FLSA Issues
    # {
    #     "problem_description": "I haven't been paid wages in two weeks and I'm owed overtime.",
    #     "expected_code": "22 Wage Claims and Other FLSA Issues",
    #     "case_name": "unpaid_wages",
    # },
    # # 25 Employee Rights
    # {
    #     "problem_description": "My boss is retaliating against me for reporting safety violations.",
    #     "expected_code": "25 Employee Rights",
    #     "case_name": "employee_retaliation",
    # },
    # # 30 Adoption
    # {
    #     "problem_description": "I want to adopt my partner's child from a previous relationship.",
    #     "expected_code": "30 Adoption",
    #     "case_name": "adoption",
    # },
    # # 31 Custody/Visitation
    # {
    #     "problem_description": "My ex is threatening to take my kids.",
    #     "expected_code": "31 Custody/Visitation",
    #     "case_name": "custody",
    # },
    # # 32 Divorce/Sep./Annul.
    # {
    #     "problem_description": "I want to divorce my spouse and divide property.",
    #     "expected_code": "32 Divorce/Sep./Annul.",
    #     "case_name": "divorce",
    # },
    # # 33 Adult Guardianship / Conservatorship
    # {
    #     "problem_description": "My elderly parent has dementia and I need to be their guardian.",
    #     "expected_code": "33 Adult Guardianship / Conservatorship",
    #     "case_name": "guardianship",
    # },
    # # 37 Domestic Abuse
    # {
    #     "problem_description": "My partner is physically abusive and I need a restraining order.",
    #     "expected_code": "37 Domestic Abuse",
    #     "case_name": "domestic_abuse",
    # },
    # # 38 Support
    # {
    #     "problem_description": "My ex owes me three months of child support.",
    #     "expected_code": "38 Support",
    #     "case_name": "child_support",
    # },
    # # 51 Medicaid
    # {
    #     "problem_description": "The state denies my Medicaid application.",
    #     "expected_code": "51 Medicaid",
    #     "case_name": "medicaid",
    # },
    # # 52 Medicare
    # {
    #     "problem_description": "Medicare is denying coverage for my medication.",
    #     "expected_code": "52 Medicare",
    #     "case_name": "medicare",
    # },
    # # 61 Federally Subsidized Housing
    # {
    #     "problem_description": "I didn't receive my subsidized rent voucher this month.",
    #     "expected_code": "61 Federally Subsidized Housing",
    #     "case_name": "section_8_housing",
    # },
    # # 62 Homeownership/Real Property (Not Foreclosure)
    # {
    #     "problem_description": "The city is seizing my property for a highway project.",
    #     "expected_code": "62 Homeownership/Real Property (Not Foreclosure)",
    #     "case_name": "property_seizure",
    # },
    # # 63 Private Landlord/Tenant
    # {
    #     "problem_description": "My landlord is not providing heat in the apartment.",
    #     "expected_code": "63 Private Landlord/Tenant",
    #     "case_name": "landlord_tenant",
    # },
    # # 64 Public Housing
    # {
    #     "problem_description": "The public housing authority wants to evict me without cause.",
    #     "expected_code": "64 Public Housing",
    #     "case_name": "public_housing_eviction",
    # },
    # # 66 Housing Discrimination
    # {
    #     "problem_description": "The landlord refused to rent to me because of my race.",
    #     "expected_code": "66 Housing Discrimination",
    #     "case_name": "housing_discrimination",
    # },
    # # 67 Mortgage Foreclosures (Not Predatory Lending/Practices)
    # {
    #     "problem_description": "The bank is foreclosing on my house.",
    #     "expected_code": "67 Mortgage Foreclosures (Not Predatory Lending/Practices)",
    #     "case_name": "foreclosure",
    # },
    # # 71 TANF
    # {
    #     "problem_description": "The state cut off my TANF benefits.",
    #     "expected_code": "71 TANF",
    #     "case_name": "tanf",
    # },
    # # 73 Food Stamps
    # {
    #     "problem_description": "I was denied food stamp benefits.",
    #     "expected_code": "73 Food Stamps",
    #     "case_name": "food_stamps",
    # },
    # # 76 Unemployment Compensation
    # {
    #     "problem_description": "My unemployment claim was denied.",
    #     "expected_code": "76 Unemployment Compensation",
    #     "case_name": "unemployment",
    # },
    # # 81 Immigration/Naturalization
    # {
    #     "problem_description": "I'm on a visa and need help applying for permanent residency.",
    #     "expected_code": "81 Immigration/Naturalization",
    #     "case_name": "immigration",
    # },
    # # 84 Disability Rights
    # {
    #     "problem_description": "I need reasonable accommodations for my wheelchair.",
    #     "expected_code": "84 Disability Rights",
    #     "case_name": "disability_rights",
    # },
    # # 85 Civil Rights
    # {
    #     "problem_description": "Police arrested me while I was protesting.",
    #     "expected_code": "85 Civil Rights",
    #     "case_name": "civil_rights",
    # },
    # # 93 Licenses (Drivers, Occupational, and Others)
    # {
    #     "problem_description": "The DMV suspended my driver's license.",
    #     "expected_code": "93 Licenses (Drivers, Occupational, and Others)",
    #     "case_name": "driver_license",
    # },
    # # 95 Wills and Estates
    # {
    #     "problem_description": "My family is disputing my parent's will.",
    #     "expected_code": "95 Wills and Estates",
    #     "case_name": "estate",
    # },
    # # 96 Advanced Directives/Powers of Attorney
    # {
    #     "problem_description": "I need to create a living will and power of attorney.",
    #     "expected_code": "96 Advanced Directives/Powers of Attorney",
    #     "case_name": "power_of_attorney",
    # },
]


@pytest.fixture
def classifier():
    """Initialize classifier with configured models."""
    # Create classifier, then override enabled_models with test configuration
    clf = Classifier()
    clf.enabled_models = ENABLED_MODELS
    clf.providers = clf._init_providers()
    return clf


async def _test_single_case(classifier, test_case):
    """Run a single classification test."""
    problem_description = test_case["problem_description"]
    expected_code = test_case["expected_code"]
    case_name = test_case["case_name"]

    # Run classification
    response = await classifier.classify(problem_description=problem_description)

    # Extract the legal problem code from response
    actual_code = response.legal_problem_code

    # Print for debugging
    print(f"\n{case_name}: {problem_description[:60]}...")
    print(f"  Expected: {expected_code}")
    print(f"  Got: {actual_code}")
    print(f"  Confidence: {response.confidence}")
    print(f"  Is Eligible: {response.is_eligible}")
    if response.follow_up_questions:
        print(f"  Follow-up Questions: {len(response.follow_up_questions)} question(s)")

    assert actual_code == expected_code, (
        f"Incorrect code for '{case_name}'.\nExpected: {expected_code}\nGot: {actual_code}"
    )

    # Verify is_eligible is set correctly (should be False for codes starting with "00")
    if expected_code.startswith("00"):
        assert response.is_eligible is False, (
            f"Code '{expected_code}' starts with '00' and should have is_eligible=False"
        )
    else:
        assert response.is_eligible is True, (
            f"Code '{expected_code}' does not start with '00' and should have is_eligible=True"
        )


@pytest.mark.asyncio
async def test_all_classifications_concurrent(classifier):
    """Run all classification tests concurrently.

    This runs all test cases in parallel, which significantly speeds up
    testing when using LLM providers (API calls happen concurrently).
    For keyword-only testing, there's no performance difference but it's
    still efficient.

    Collects all failures and reports them together instead of halting
    on the first failure.
    """
    import asyncio

    # Create tasks for all test cases
    tasks = [_test_single_case(classifier, test_case) for test_case in TEST_CASES]

    # Run all tasks concurrently, collecting exceptions instead of stopping on first failure
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Check for any failures and report them all
    failures = [r for r in results if isinstance(r, Exception)]
    if failures:
        failure_messages = "\n".join(f"  - {str(f)}" for f in failures)
        pytest.fail(f"Failed {len(failures)} test case(s):\n{failure_messages}")
