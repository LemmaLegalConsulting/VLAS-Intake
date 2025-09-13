from fastapi import Request
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import Connect, Stream, VoiceResponse

from .env_var import get_env_var, require_env_var
from .server import logger


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
    auth_token = require_env_var("TWILIO_AUTH_TOKEN")
    domain = require_env_var("DOMAIN")

    # https://community.fly.io/t/redirect-uri-is-http-instead-of-https/6671/6
    protocol = get_env_var("PROTOCOL", "https")

    validator = RequestValidator(auth_token)
    url = f"""{protocol}://{domain}/"""
    form_data = await request.form()
    twilio_signature = request.headers.get("X-TWILIO-SIGNATURE")

    if url and form_data and twilio_signature:
        is_valid = validator.validate(uri=url, params=form_data, signature=twilio_signature)
    else:
        is_valid = False
    return is_valid
