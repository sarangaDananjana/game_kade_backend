"""Dashboard totals derived from delivery facts, with no resettable counters."""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Avg, Count, F, Q, Sum

from .models import Session, SessionOrder
from .timeutils import period_bounds


def _in_period(queryset, field, start, end):
    return queryset.filter(**{f'{field}__gte': start, f'{field}__lt': end}) if start is not None else queryset


def _percentage(numerator, denominator):
    if not denominator:
        return None
    return (Decimal(numerator) * 100 / Decimal(denominator)).quantize(Decimal('0.01'))


def rider_stats(rider_id, start_date=None, end_date=None, *, using='default'):
    if rider_id is None:
        raise ValueError('Choose a rider before reading dashboard statistics.')
    start, end = period_bounds(start_date, end_date)
    sessions = Session.objects.using(using).filter(assigned_rider_id=rider_id)
    completed = _in_period(sessions.filter(status=Session.Status.COMPLETED), 'finished_at', start, end)
    attempts = _in_period(SessionOrder.objects.using(using).filter(
        session__assigned_rider_id=rider_id, status__in=[SessionOrder.Status.DELIVERED, SessionOrder.Status.FAILED],
    ), 'finished_at', start, end)
    delivered = Q(status=SessionOrder.Status.DELIVERED)
    deadlines = delivered & Q(expected_delivery_at__isnull=False)
    outcomes = attempts.aggregate(
        delivery_attempts=Count('pk'),
        delivered_orders=Count('pk', filter=delivered),
        failed_orders=Count('pk', filter=Q(status=SessionOrder.Status.FAILED)),
        scheduled_deliveries=Count('pk', filter=deadlines),
        on_time_deliveries=Count('pk', filter=deadlines & Q(finished_at__lte=F('expected_delivery_at'))),
    )
    runs = completed.aggregate(
        completed_sessions=Count('pk'), rider_points=Sum('points_awarded', default=0),
        session_distance=Sum('session_distance', default=Decimal('0.000')),
        total_session_time=Sum('session_time', default=timedelta(0)), average_session_time=Avg('session_time'),
        unconfirmed_revenue_sessions=Count('pk', filter=Q(finance_status=Session.FinanceStatus.DRAFT)),
    )
    confirmed = _in_period(sessions.filter(finance_status=Session.FinanceStatus.CONFIRMED), 'settled_at', start, end)
    finances = confirmed.aggregate(
        rider_revenue=Sum('rider_revenue', default=Decimal('0.00')),
        total_revenue=Sum('total_revenue', default=Decimal('0.00')),
        service_charge=Sum('service_charge', default=Decimal('0.00')),
        fuel_surge_addition=Sum('fuel_surge_addition', default=Decimal('0.00')),
    )
    runs['session_time'] = runs.pop('total_session_time')
    seconds = Decimal(str(runs['session_time'].total_seconds()))
    speed = (runs['session_distance'] * 3600 / seconds).quantize(Decimal('0.01')) if seconds > 0 else None
    return {
        'start_date': start_date, 'end_date': end_date, 'currency': 'LKR',
        **outcomes, **runs, **finances,
        'completion_rate': _percentage(outcomes['delivered_orders'], outcomes['delivery_attempts']),
        'on_time_rate': _percentage(outcomes['on_time_deliveries'], outcomes['scheduled_deliveries']),
        'average_speed_kmh': speed,
    }
