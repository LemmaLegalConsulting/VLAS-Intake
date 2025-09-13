import os

from dotenv import load_dotenv
from fastapi import Request
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import Connect, Stream, VoiceResponse

from .server import logger

load_dotenv(override=True)


def create_twiml(url: str, body_data: dict = None) -> str:
    """
    Generates a TwiML (Twilio Markup Language) response as a string for initiating a voice call stream.

    Args:
        url (str): The URL to which the Stream should connect.
        body_data (dict, optional): A dictionary of parameters to include in the Stream. Each key-value pair is added as a custom parameter.

    Returns:
        str: The generated TwiML response as a string.
    """
    response = VoiceResponse()
    connect = Connect()
    stream = Stream(url=url)
    if isinstance(body_data, dict):
        for name, value in body_data.items():
            stream.parameter(name=name, value=value)
    connect.append(stream)
    response.append(connect)
    twiml = str(response)
    logger.debug(twiml)
    return twiml


async def validate_webhook(request: Request) -> bool:
    """
    Validates an incoming Twilio webhook request using the Twilio RequestValidator.

    Args:
        request (Request): The incoming HTTP request to validate.

    Returns:
        bool: True if the request is a valid Twilio webhook (signature matches), False otherwise.
    """
    if not (auth_token := os.getenv("TWILIO_AUTH_TOKEN")):
        raise ValueError("The TWILIO_AUTH_TOKEN environment variable must be set.")
    if not (domain := os.getenv("DOMAIN")):
        raise ValueError("The DOMAIN environment variable must be set.")

    # https://community.fly.io/t/redirect-uri-is-http-instead-of-https/6671/6
    protocol = os.getenv("PROTOCOL", "https")

    validator = RequestValidator(auth_token)
    url = f"""{protocol}://{domain}/"""
    form_data = await request.form()
    twilio_signature = request.headers.get("X-TWILIO-SIGNATURE")

    if url and form_data and twilio_signature:
        is_valid = validator.validate(uri=url, params=form_data, signature=twilio_signature)
    else:
        is_valid = False
    return is_valid
