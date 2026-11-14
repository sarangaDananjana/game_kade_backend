"""Sri Lankan calendar and presentation; retain each timestamp's real instant."""
from zoneinfo import ZoneInfo
from django.utils import timezone

SRI_LANKA_TIME_ZONE = ZoneInfo('Asia/Colombo')


def sri_lanka_isoformat(value):
    if timezone.is_naive(value):
        value = timezone.make_aware(value, SRI_LANKA_TIME_ZONE)
    return value.astimezone(SRI_LANKA_TIME_ZONE).isoformat()


class SriLankaTimezoneMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        with timezone.override(SRI_LANKA_TIME_ZONE):
            return self.get_response(request)
