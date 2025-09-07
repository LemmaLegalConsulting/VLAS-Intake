import os

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

url = f"""https://{os.getenv("LEGAL_SERVER_SUBDOMAIN")}.legalserver.org/api/v1/matters"""
headers = {
    "Authorization": f"""Bearer {os.getenv("LEGAL_SERVER_BEARER_TOKEN")}""",
    "Content-Type": "application/json",
}


def test_get(url: str, headers: dict):
    try:
        params = {"page_number": 1, "page_size": 1}
        response = requests.request("GET", url, headers=headers, params=params)
        if response.status_code != 200:
            print(response.status_code, response.reason)
        print(response.text)
    except requests.exceptions.RequestException as e:
        print("HTTP Request failed", e)


def test_post(url: str, headers: dict):
    try:
        payload = {
            "first": "John",
            "last": "Doe",
            "case_disposition": "Incomplete Intake",
            "case_type": "Online Intake",
        }
        response = requests.request("POST", url, headers=headers, json=payload)
        if response.status_code != 200:
            print(response.status_code, response.reason)
        print(response.text)
    except requests.exceptions.RequestException as e:
        print("HTTP Request failed", e)


if __name__ == "__main__":
    test_get(url, headers)
    print()
    test_post(url, headers)
