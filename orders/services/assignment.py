"""One assignment policy for the platform dashboard and dispatch APIs."""

from django.contrib.auth import get_user_model

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


def assign_orders(order_ids, rider_id=None, *, unassign=False):
    if not isinstance(order_ids, list) or not order_ids or len(order_ids) > 1000:
        raise AssignmentError('Select between 1 and 1,000 orders.')
    order_ids = {_positive_id(value) for value in order_ids}
    rider = None
    if not unassign:
        rider = get_user_model().objects.filter(pk=_positive_id(rider_id), role='rider', is_active=True).first()
        if rider is None:
            raise AssignmentError('Choose an active registered rider.')
    count = Order.objects.filter(pk__in=order_ids).exclude(status__in=['cancelled', 'delivered']).update(rider=rider)
    return rider, count
