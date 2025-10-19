import phonenumbers


def phone_number_is_valid(phone_number: str, region: str = "US") -> tuple[bool, str]:
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
