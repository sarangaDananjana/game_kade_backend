"""Numeric handover codes shared by customer orders and rider confirmation."""

import secrets
from datetime import timezone as datetime_timezone
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.utils import timezone


DELIVERY_TIME_ZONE = ZoneInfo('Asia/Colombo')
DAILY_CODE_CAPACITY = 10_000


def delivery_code_prefix(created_at=None):
    timestamp = created_at or timezone.now()
    if timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp, datetime_timezone.utc)
    return timestamp.astimezone(DELIVERY_TIME_ZONE).strftime('%y%m%d')


def generate_delivery_code(*, created_at=None, using='default'):
    # Import lazily so Order can call this from save without a model import cycle.
    from multivender.models import Order

    prefix = delivery_code_prefix(created_at)
    used = set(Order.objects.using(using).filter(
        delivery_code__startswith=prefix,
    ).values_list('delivery_code', flat=True))
    available = [f'{prefix}{suffix:04d}' for suffix in range(DAILY_CODE_CAPACITY)
                 if f'{prefix}{suffix:04d}' not in used]
    if not available:
        raise ValidationError(
            'All 10,000 delivery codes for this order date have been used.',
            code='delivery_codes_exhausted',
        )
    return secrets.choice(available)


def delivery_code_matches(order, provided_code):
    if not isinstance(provided_code, (str, int)) or isinstance(provided_code, bool):
        return False
    provided = str(provided_code).strip()
    expected = str(order.delivery_code)
    if not provided or not provided.isascii():
        return False
    if secrets.compare_digest(expected, provided):
        return True
    if (len(expected) == 10 and expected.isascii() and expected.isdigit()
            and len(provided) == 4 and provided.isascii() and provided.isdigit()):
        return secrets.compare_digest(expected[-4:], provided)
    # Previously issued UUID QR codes remain valid for their original order.
    legacy = getattr(order, 'legacy_delivery_code', None)
    return legacy is not None and secrets.compare_digest(str(legacy), provided)
