import phonenumbers


def phone_number_is_valid(phone_number: str, region: str = "US") -> tuple[bool, str]:
    """
    Validates a phone number for a given region and returns formatted number if valid.

    Args:
        phone_number (str): The phone number string to validate.
        region (str): The region code (ISO 3166-1 alpha-2) to validate against. Defaults to "US".

    Returns:
        tuple[bool, str]: A tuple containing:
            - bool: True if the phone number is valid for the specified region, False otherwise.
            - str: The formatted phone number in NATIONAL format if valid, otherwise the original phone_number string.

    Raises:
        No exceptions are raised; invalid phone numbers return (False, phone_number).
    """
    try:
        parsed = phonenumbers.parse(phone_number, region)
        valid = (
            phonenumbers.is_valid_number(parsed)
            and phonenumbers.region_code_for_number(parsed) == region
        )
        if valid:
            phone_number = phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.NATIONAL
            )
    except phonenumbers.phonenumberutil.NumberParseException:
        valid = False
    return valid, phone_number
