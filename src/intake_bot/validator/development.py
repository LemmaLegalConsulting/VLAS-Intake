import asyncio

import httpx
from intake_bot.utils.globals import LEGALSERVER_API_BASE_URL, LEGALSERVER_HEADERS
from intake_bot.validator.validator import IntakeValidator


def validator_check_case_type(case_description: str) -> dict:
    import asyncio

    async def _check_case_type_async(case_description: str) -> dict:
        validator = IntakeValidator()
        return await validator.check_case_type(case_description)

    return asyncio.run(_check_case_type_async(case_description))


def validator_check_conflict():
    from intake_bot.models.validators import PotentialConflicts
    from intake_bot.validator.validator import IntakeValidator

    validator = IntakeValidator()
    pc = PotentialConflicts([dict(first="Aldous", last="Snow")])
    r = asyncio.run(validator.check_conflict(pc))
    print(r)


def dev_legalserver():
    def test_get_matters():
        url = LEGALSERVER_API_BASE_URL + "matters"
        try:
            params = {
                "page_number": 1,
                "page_size": 1,
                "first": "Dexter",
                "last": "Campbell",
            }
            response = httpx.get(url, headers=LEGALSERVER_HEADERS, params=params)
            if response.status_code != 200:
                print(response.status_code, response.reason)
            print(response.text)
        except httpx.exceptions.RequestException as e:
            print("HTTP Request failed", e)

    def test_post_matters():
        url = LEGALSERVER_API_BASE_URL + "matters"
        try:
            # payload = {
            #     "first": "Jimmy",
            #     "last": "Dean",
            #     "case_disposition": "Incomplete Intake",
            # }

            payload = {
                "first": "Dexter",
                "last": "Campbell",
                "middle": "",
                "is_group": False,
                "case_disposition": "Incomplete Intake",
                "mobile_phone": "8665345243",
                # "mobile_phone_safe": True,
                # "county_of_dispute": {
                #     "county_name": "Amelia",
                #     "county_state": "VA",
                # },
                # "percentage_of_poverty": "0%",
                # "asset_eligible": True,
                # "lsc_eligible": True,
                # "income_eligible": True,
                # "victim_of_domestic_violence": True,
                "legal_problem_code": "91 Legal Assist. to Non-Profit Org. or Group (Incl. Incorp./Diss.)",
            }

            response = httpx.post(url, headers=LEGALSERVER_HEADERS, json=payload)
            if response.status_code != 200:
                print(response.status_code, response.reason)
            print(response.text)
        except httpx.exceptions.RequestException as e:
            print("HTTP Request failed", e)

    def test_post_record_conflict():
        url = LEGALSERVER_API_BASE_URL + "conflict_check"
        try:
            payload = {
                "first": "Dexter",
                "last": "Campbell",
            }
            print(payload)
            response = httpx.post(url, headers=LEGALSERVER_HEADERS, json=payload)
            if response.status_code != 200:
                print(response.status_code, response.reason)
            print(response.text)
        except httpx.exceptions.RequestException as e:
            print("HTTP Request failed", e)
        except Exception as e:
            print("Exception", e)

    # test_post_matters()
    # print()
    # test_get_matters()
    # print()
    test_post_record_conflict()


if __name__ == "__main__":
    # validator_check_case_type(case_description="desc")
    # validator_check_conflict()
    # dev_legalserver()
    pass
