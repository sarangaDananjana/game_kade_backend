"""Calendar boundaries used by rider reports."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone


RIDER_TIME_ZONE = ZoneInfo('Asia/Colombo')


def rider_local_date():
    return timezone.now().astimezone(RIDER_TIME_ZONE).date()


def period_bounds(start_date, end_date):
    """Inclusive start date, exclusive end date, in Sri Lankan local time."""
    if (start_date is None) != (end_date is None):
        raise ValueError('Provide both the start and end date.')
    if start_date is None:
        return None, None
    if start_date >= end_date:
        raise ValueError('The end date must be after the start date.')
    return (datetime.combine(start_date, time.min, tzinfo=RIDER_TIME_ZONE),
            datetime.combine(end_date, time.min, tzinfo=RIDER_TIME_ZONE))


def week_dates(reference_date=None):
    reference_date = reference_date or rider_local_date()
    start = reference_date - timedelta(days=reference_date.weekday())
    return start, start + timedelta(days=7)


def month_dates(reference_date=None):
    reference_date = reference_date or rider_local_date()
    start = reference_date.replace(day=1)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, end
