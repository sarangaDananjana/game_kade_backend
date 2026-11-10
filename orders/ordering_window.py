"""Current ordering hours shared by every customer checkout API."""

from zoneinfo import ZoneInfo

from django.utils import timezone
from rest_framework import permissions
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import OrderingWindow


ORDERING_TIME_ZONE = ZoneInfo('Asia/Colombo')


def display_time(value):
    return f'{value.hour % 12 or 12}:{value.minute:02d}' + (
        f':{value.second:02d}' if value.second else ''
    ) + (' AM' if value.hour < 12 else ' PM')


def current_ordering_window(at=None):
    # Do not cache: an admin change must apply to the next checkout request.
    window = OrderingWindow.objects.filter(pk=1).first() or OrderingWindow()
    local_now = (at if at is not None else timezone.now()).astimezone(ORDERING_TIME_ZONE)
    local_time = local_now.time()
    overnight = window.closes_at < window.opens_at
    within_hours = (
        local_time >= window.opens_at or local_time < window.closes_at
        if overnight else window.opens_at <= local_time < window.closes_at
    )
    is_open = window.enabled and within_hours
    opens_display, closes_display = display_time(window.opens_at), display_time(window.closes_at)
    hours_display = f'{opens_display} to {closes_display}' + (' next day' if overnight else '')
    if not window.enabled:
        message = 'Ordering is temporarily paused. Please try again later.'
    elif is_open:
        message = f'Orders are accepted daily from {hours_display} (Sri Lanka time).'
    else:
        message = f'Ordering is closed right now. Orders are accepted daily from {hours_display} (Sri Lanka time).'
    return {
        'enabled': window.enabled,
        'is_open': is_open,
        'opens_at': window.opens_at.isoformat(),
        'closes_at': window.closes_at.isoformat(),
        'opens_at_display': opens_display,
        'closes_at_display': closes_display,
        'hours_display': hours_display,
        'timezone': str(ORDERING_TIME_ZONE),
        'server_time': local_now.isoformat(),
        'message': message,
    }


def ensure_ordering_open():
    window = current_ordering_window()
    if not window['is_open']:
        raise ValidationError({'detail': window['message'], 'code': 'ordering_closed'})


class OrderingWindowView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        return Response(current_ordering_window(), headers={'Cache-Control': 'no-store'})
