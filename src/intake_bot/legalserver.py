import os

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

API_URL = f"""https://{os.getenv("LEGAL_SERVER_SUBDOMAIN")}.legalserver.org/api/v2/"""
headers = {
    "Authorization": f"""Bearer {os.getenv("LEGAL_SERVER_BEARER_TOKEN")}""",
    "Content-Type": "application/json",
    "Accept": "application/json, text/html",
}


def test_get(endpoint_url: str, headers: dict):
    url = API_URL + endpoint_url
    try:
        params = {
            "page_number": 1,
            "page_size": 1,
            "first": "Dexter",
            "last": "Campbell",
        }
        response = requests.get(url, headers=headers, params=params)
        if response.status_code != 200:
            print(response.status_code, response.reason)
        print(response.text)
    except requests.exceptions.RequestException as e:
        print("HTTP Request failed", e)


def test_post(endpoint_url: str, headers: dict):
    url = API_URL + endpoint_url
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

        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            print(response.status_code, response.reason)
        print(response.text)
    except requests.exceptions.RequestException as e:
        print("HTTP Request failed", e)


def test_conflict_check(endpoint_url: str, headers: dict):
    url = API_URL + endpoint_url
    try:
        payload = {
            "first": "Dexter",
            "last": "Campbell",
        }
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            print(response.status_code, response.reason)
        print(response.text)
    except requests.exceptions.RequestException as e:
        print("HTTP Request failed", e)


if __name__ == "__main__":
    test_post("matters", headers)
    # print()
    # test_get("matters", headers)
    # print()
    # test_conflict_check("conflict_check", headers)
