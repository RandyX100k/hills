# Phone helpers. WhatsApp identifies a customer by the E.164 number without the plus
# sign (what Meta calls the wa_id); Odoo stores numbers in whatever format the user typed.

import re

try:
    import phonenumbers
except ImportError:  # the manifest declares the dependency; keep imports safe for tooling
    phonenumbers = None

_DIGITS = re.compile(r'\D+')


def digits(value):
    """'+57 (321) 474-1592' -> '573214741592'. Never raises."""
    return _DIGITS.sub('', value or '')


def to_wa_id(value, country_code=None):
    """Normalise a phone to the wa_id form, using the country when the number is local.

    Returns None when the number cannot be parsed as a real phone number."""
    if not value or not phonenumbers:
        return digits(value) or None
    raw = value.strip()
    candidates = [raw]
    if not raw.startswith('+'):
        candidates.append('+' + digits(raw))
    for candidate in candidates:
        try:
            parsed = phonenumbers.parse(candidate, country_code or None)
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_possible_number(parsed):
            return str(parsed.country_code) + str(parsed.national_number)
    return None


def to_display(wa_id):
    """'573214741592' -> '+57 321 4741592' for humans. Falls back to '+<digits>'."""
    if not wa_id:
        return ''
    if phonenumbers:
        try:
            parsed = phonenumbers.parse('+' + wa_id, None)
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
        except phonenumbers.NumberParseException:
            pass
    return '+' + wa_id


def region_of(wa_id):
    """ISO-2 region of a wa_id, or None."""
    if not wa_id or not phonenumbers:
        return None
    try:
        return phonenumbers.region_code_for_number(phonenumbers.parse('+' + wa_id, None))
    except phonenumbers.NumberParseException:
        return None
