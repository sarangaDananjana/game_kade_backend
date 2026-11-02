"""Numeric handover codes shared by customer orders and rider confirmation."""

import secrets
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.utils import timezone


DELIVERY_TIME_ZONE = ZoneInfo('Asia/Colombo')
DAILY_CODE_CAPACITY = 10_000


def delivery_code_prefix(created_at=None):
    timestamp = created_at or timezone.now()
    if timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp, DELIVERY_TIME_ZONE)
    return timestamp.astimezone(DELIVERY_TIME_ZONE).strftime('%y%m%d')


def generate_delivery_code(*, created_at=None, using='default'):
    # Import lazily so Order can call this from save without a model import cycle.
    from orders.models import Order

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


def resolve_today_delivery_code(provided_code):
    if not isinstance(provided_code, (str, int)) or isinstance(provided_code, bool):
        return None
    provided = str(provided_code).strip()
    if not provided.isascii() or not provided.isdigit() or len(provided) not in [4, 10]:
        return None
    prefix = delivery_code_prefix()
    candidate = prefix + provided if len(provided) == 4 else provided
    return candidate if candidate.startswith(prefix) else None


def delivery_code_matches(order, provided_code):
    candidate = resolve_today_delivery_code(provided_code)
    if candidate is None:
        return False
    created_at = getattr(order, 'created_at', None)
    if created_at is not None and delivery_code_prefix(created_at) != candidate[:6]:
        return False
    return secrets.compare_digest(str(order.delivery_code), candidate)
