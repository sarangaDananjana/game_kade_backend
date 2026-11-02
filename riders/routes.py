"""Cluster shared order locations and rank stops outward from the session's kitchen."""

from math import atan2, cos, radians, sin, sqrt


def distance_km(lat1, lng1, lat2, lng2):
    lat1, lat2, dlat, dlng = radians(lat1), radians(lat2), radians(lat2 - lat1), radians(lng2 - lng1)
    term = sin(dlat / 2)**2 + cos(lat1) * cos(lat2) * sin(dlng / 2)**2
    return 6371.0088 * 2 * atan2(sqrt(term), sqrt(max(0, 1 - term)))


def session_stops(session):
    links = session.session_orders.select_related('order__user', 'order__location', 'order__vendor').prefetch_related('order__items__product')
    grouped = {}
    for link in links:
        order = link.order
        if order is None or order.location is None:
            continue
        location = order.location
        key = (round(location.lat, 6), round(location.lng, 6))
        if key not in grouped:
            grouped[key] = {'location_id': location.pk, 'name': location.name, 'lat': location.lat, 'lng': location.lng,
                'description': location.description or '', 'landmark': location.unique_identity, 'orders': []}
        # Verification secrets are deliberately absent from rider responses.
        grouped[key]['orders'].append({'id': order.pk, 'number': f'#{order.pk:04d}', 'attempt_id': link.pk,
            'status': link.status, 'customer_name': order.user.name or 'Customer', 'customer_phone': order.user.phone_number,
            'total_amount': str(order.total_amount), 'delivery_fee': str(link.delivery_fee),
            'items': [{'name': item.product.name if item.product else 'Meal', 'quantity': item.quantity,
                       'price': str(item.price_at_purchase)} for item in order.items.all()]})
    stops = list(grouped.values())
    if session.vendor:
        for stop in stops:
            stop['distance_from_vendor'] = round(distance_km(session.vendor.lat, session.vendor.lng, stop['lat'], stop['lng']), 3)
        stops.sort(key=lambda stop: (stop['distance_from_vendor'], stop['location_id']))
    for number, stop in enumerate(stops, 1):
        stop['number'] = number
        stop['completed'] = all(order['status'] != 'queued' for order in stop['orders'])
        stop['remaining_orders'] = sum(order['status'] == 'queued' for order in stop['orders'])
    return stops
