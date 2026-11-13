import requests
import secrets
import os
import logging
from django.conf import settings
from .phone_numbers import normalize_mobile_number

logger = logging.getLogger(__name__)


class SmsDeliveryUncertain(Exception):
    """The provider may have sent the SMS before its response failed."""


def should_bypass_otp_sms(phone):
    phone = normalize_mobile_number(phone)
    return phone in getattr(settings, 'OTP_SMS_BYPASS_NUMBERS', set())


def find_user_by_mobile(phone):
    from .models import CustomUser
    canonical = CustomUser.objects.filter(phone_number=phone).first()
    if canonical:
        return canonical
    international = '94' + phone[1:]
    return CustomUser.objects.filter(phone_number__in=[international, '+' + international, '00' + international]).first()


def generate_otp_code():
    """Generate an unpredictable six-digit login code."""
    return str(secrets.randbelow(900000) + 100000)


def send_otp_sms(phone, otp_code):
    """Sends OTP using the provided text.lk API"""
    try:
        phone = normalize_mobile_number(phone)
    except ValueError:
        return False
    if should_bypass_otp_sms(phone):
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
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            json={
                "recipient": '94' + phone[1:],
                "sender_id": "One Mind",
                "type": "plain",
                "message": message
            },
            timeout=5,
            allow_redirects=False,
        )
        if not 200 <= response.status_code < 300:
            logger.warning('SMS provider returned HTTP %s.', response.status_code)
            return False
        # Text.lk may return HTTP 200 for an application-level error.
        try:
            result = response.json()
        except ValueError:
            logger.warning('SMS provider returned an unreadable success response; delivery is uncertain.')
            raise SmsDeliveryUncertain from None
        provider_status = str(result.get('status', '')).lower() if isinstance(result, dict) else ''
        if provider_status == 'success':
            return True
        if provider_status in {'error', 'fail', 'failed'}:
            logger.warning('SMS provider did not accept the login message.')
            return False
        logger.warning('SMS provider returned an unknown success response; delivery is uncertain.')
        raise SmsDeliveryUncertain
    except requests.exceptions.ConnectTimeout:
        logger.warning('SMS provider connection timed out before delivery.')
        return False
    except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError, requests.exceptions.ContentDecodingError):
        # A lost response can happen after Text.lk has accepted/sent the code.
        # Keep that code verifiable rather than invalidating an arriving SMS.
        logger.warning('SMS provider response was lost; delivery is uncertain.')
        raise SmsDeliveryUncertain from None
    except requests.exceptions.RequestException as e:
        logger.warning('SMS request failed (%s).', type(e).__name__)
        return False
