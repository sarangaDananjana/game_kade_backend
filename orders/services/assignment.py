"""One assignment policy for the platform dashboard and dispatch APIs."""

from django.contrib.auth import get_user_model
from django.db import transaction

from orders.models import Order


class AssignmentError(ValueError):
    pass


def _positive_id(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise AssignmentError('Choose valid order and rider IDs.')
    text = str(value).strip()
    if not text.isascii() or not text.isdigit() or not 0 < int(text) <= 2**63 - 1:
        raise AssignmentError('Choose valid order and rider IDs.')
    return int(text)


@transaction.atomic
def assign_orders(order_ids, rider_id=None, *, unassign=False):
    if not isinstance(order_ids, list) or not order_ids or len(order_ids) > 1000:
        raise AssignmentError('Select between 1 and 1,000 orders.')
    order_ids = {_positive_id(value) for value in order_ids}
    active_orders = Order.objects.select_for_update().filter(pk__in=order_ids).exclude(status__in=['cancelled', 'delivered'])
    active_ids = list(active_orders.values_list('pk', flat=True))
    from riders.models import SessionOrder
    if SessionOrder.objects.filter(order_reference__in=active_ids, status__in=['queued', 'delivered']).exists():
        raise AssignmentError('These orders belong to a delivery session. Manage their rider from the session planner.')
    rider = None
    if not unassign:
        rider = get_user_model().objects.filter(pk=_positive_id(rider_id), role='rider', is_active=True).first()
        if rider is None:
            raise AssignmentError('Choose an active registered rider.')
        from riders.profile import profile_completion
        if not hasattr(rider, 'rider_info') or not profile_completion(rider.rider_info)['is_complete']:
            raise AssignmentError('This rider must complete their name, vehicle details, and emergency contact first.')
    count = Order.objects.filter(pk__in=active_ids).update(rider=rider)
    return rider, count
