import requests
import random
import os  # <-- Add this
import re
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


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


def find_user_by_mobile(phone):
    from .models import CustomUser
    canonical = CustomUser.objects.filter(phone_number=phone).first()
    if canonical:
        return canonical
    international = '94' + phone[1:]
    return CustomUser.objects.filter(phone_number__in=[international, '+' + international, '00' + international]).first()


def generate_otp_code():
    """Generates a 6-digit random OTP"""
    return str(random.randint(100000, 999999))


def send_otp_sms(phone, otp_code):
    """Sends OTP using the provided text.lk API"""
    try:
        phone = normalize_mobile_number(phone)
    except ValueError:
        return False
    if phone in getattr(settings, 'OTP_SMS_BYPASS_NUMBERS', {'0721992125', '0774610536'}) or phone == '0700000000':
        return True
    message = f"Your Meal Drop OTP code is: {otp_code}. It will expire in 5 minutes."
    api_key = os.environ.get("TEXT_LK_API_KEY")
    if not api_key:
        logger.warning('SMS service key is not configured.')
        return False

    try:
        response = requests.post(
            "https://app.text.lk/api/v3/sms/send",
            headers={
                # Note: In production, move this Bearer token to .env
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            json={
                "recipient": '94' + normalize_mobile_number(phone)[1:],
                "sender_id": "One Mind",
                "type": "plain",
                "message": message
            },
            timeout=5
        )
        if not 200 <= response.status_code < 300:
            logger.warning('SMS provider returned HTTP %s.', response.status_code)
            return False
        # Text.lk may return HTTP 200 for an application-level error.
        result = response.json()
        success = isinstance(result, dict) and str(result.get('status', '')).lower() == 'success'
        if not success:
            logger.warning('SMS provider did not accept the login message.')
        return success
    except (requests.exceptions.RequestException, ValueError) as e:
        logger.warning('SMS request failed (%s).', type(e).__name__)
        return False
