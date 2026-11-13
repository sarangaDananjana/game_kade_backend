"""Sri Lankan mobile-number normalization shared by settings and login."""

import re


def normalize_mobile_number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError('Enter a valid Sri Lankan mobile number.')
    text = str(value).strip()
    if not re.fullmatch(r'[0-9+ ()-]+', text):
        raise ValueError('Enter a valid Sri Lankan mobile number.')
    digits = re.sub(r'[^0-9]', '', text)
    if digits.startswith('0094'):
        digits = digits[4:]
    elif digits.startswith('94'):
        digits = digits[2:]
    if len(digits) == 9 and digits.startswith('7'):
        digits = '0' + digits
    if len(digits) != 10 or not digits.startswith('07'):
        raise ValueError('Enter a mobile number such as 0771234567.')
    return digits
