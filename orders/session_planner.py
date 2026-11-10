"""Session-authenticated map planning for platform administrators."""

import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from math import isfinite

from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from rest_framework import serializers

from riders.models import JobOffer, RiderInfo, Session, SessionOrder
from riders.profile import profile_completion
from riders.routes import distance_km
from riders.serializers import SessionCreateSerializer
from riders.services import RiderActionError, create_session, offer_session
from riders.timeutils import RIDER_TIME_ZONE, rider_local_date
from users.models import CustomUser
from vendors.permissions import is_platform_admin

from .models import Order
from .service_area import primary_delivery_zone


def _error(detail, status=400, code='invalid_request', fields=None):
    data = {'error': detail, 'code': code}
    if fields:
        data['fields'] = fields
    return JsonResponse(data, status=status)


def _request_json(request):
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise ValueError('Send a valid JSON object.')
    if not isinstance(data, dict):
        raise ValueError('Send a valid JSON object.')
    return data


def _coordinates(location):
    if location is None:
        return False
    try:
        return (isfinite(location.lat) and isfinite(location.lng)
                and -90 <= location.lat <= 90 and -180 <= location.lng <= 180)
    except TypeError:
        return False


def _location(location):
    if location is None:
        return None
    return {'id': location.pk,
            'name': location.name if location.is_system_defined else location.unique_identity,
            'description': location.description or '', 'lat': location.lat, 'lng': location.lng,
            'is_system_defined': location.is_system_defined}


def _vendor(vendor):
    if vendor is None:
        return None
    return {'id': vendor.pk, 'name': vendor.shop_name, 'lat': vendor.lat, 'lng': vendor.lng,
            'is_active': vendor.is_active}


def _blocked_reason(order, zone, attempt=None):
    if order.status in ['delivered', 'cancelled']:
        return order.get_status_display()
    if attempt is not None:
        return f'In session #{attempt.session_id}'
    if order.rider_id:
        return 'Already assigned to a rider'
    if not order.delivery_fee.is_finite() or order.delivery_fee < 0:
        return 'Invalid delivery fee'
    if order.status not in ['pending', 'preparing']:
        return 'Delivery already started'
    if not order.vendor.is_active or order.vendor.is_pickup_only:
        return 'Kitchen is unavailable for delivery'
    if not _coordinates(order.location):
        return 'A delivery location is required'
    if zone is None or zone.polygon.empty or not zone.polygon.valid:
        return 'Configure the delivery area before planning'
    if not zone.polygon.covers(Point(order.location.lng, order.location.lat, srid=4326)):
        return 'Outside the delivery area'
    return ''


def _session_queryset():
    return Session.objects.select_related('vendor', 'assigned_rider').prefetch_related(
        Prefetch('session_orders', queryset=SessionOrder.objects.select_related('order__location'),
                 to_attr='planning_links'),
        Prefetch('job_offers', queryset=JobOffer.objects.select_related('rider').order_by('-offered_at', '-pk'),
                 to_attr='planning_offers'),
    )


def _session_data(session, now):
    grouped = {}
    for link in session.planning_links:
        if link.order is None or not _coordinates(link.order.location):
            continue
        location = link.order.location
        if location.pk not in grouped:
            grouped[location.pk] = {**_location(location), 'order_ids': [], 'remaining_orders': 0}
        grouped[location.pk]['order_ids'].append(link.order_reference)
        grouped[location.pk]['remaining_orders'] += link.status == 'queued'
    stops = list(grouped.values())
    if session.vendor:
        stops.sort(key=lambda stop: (distance_km(session.vendor.lat, session.vendor.lng,
                                               stop['lat'], stop['lng']), stop['id']))
    for number, stop in enumerate(stops, 1):
        stop['number'] = number
    pending = next((offer for offer in session.planning_offers if offer.status == 'pending'), None)
    latest = pending or next(iter(session.planning_offers), None)
    offer = None
    if latest:
        offer = {'id': latest.pk, 'status': 'expired' if latest.status == 'pending' and latest.expires_at <= now else latest.status,
                 'rider_id': latest.rider_id, 'rider_name': latest.rider.name if latest.rider else 'Deleted rider',
                 'rider_phone': latest.rider.phone_number if latest.rider else '',
                 'acceptance_window': latest.acceptance_window,
                 'expires_at': timezone.localtime(latest.expires_at, RIDER_TIME_ZONE).isoformat()}
    queued = sum(link.status == 'queued' for link in session.planning_links)
    return {'id': session.pk, 'date': session.date.isoformat(), 'status': session.status,
            'status_display': session.get_status_display(), 'vendor': _vendor(session.vendor),
            'rider_name': session.assigned_rider.name if session.assigned_rider else None,
            'order_ids': [link.order_reference for link in session.planning_links],
            'order_count': len(session.planning_links), 'stop_count': len(stops), 'stops': stops,
            'rider_revenue': format(session.rider_revenue, '.2f'),
            'service_charge': format(session.service_charge, '.2f'),
            'finance_status': session.finance_status, 'dispatch_enabled': session.dispatch_enabled,
            'offer': offer, 'can_offer': session.status == 'planned' and session.assigned_rider_id is None
            and queued > 0 and bool(session.vendor and session.vendor.is_active)
            and session.date == rider_local_date() and not (pending and pending.expires_at > now),
            'admin_url': reverse('admin:riders_session_change', args=[session.pk])}


@ensure_csrf_cookie
@require_GET
def session_planner_view(request):
    if not is_platform_admin(request.user):
        return redirect(f"{reverse('dispatch-login')}?next={request.path}")
    return render(request, 'orders/session_planner.html', {
        'today': rider_local_date().isoformat(),
        'planner_config': {'dataUrl': reverse('dispatch-planning-data'),
                           'createUrl': reverse('dispatch-create-session'),
                           'offerUrl': reverse('dispatch-offer-session', args=[0]),
                           'loginUrl': reverse('dispatch-login')},
    })


@require_GET
def planning_data_api(request):
    if not is_platform_admin(request.user):
        return _error('Admin credentials required.', 403, 'permission_denied')
    try:
        selected_date = date.fromisoformat(request.GET.get('date') or rider_local_date().isoformat())
        if request.GET.get('date') and request.GET['date'] != selected_date.isoformat():
            raise ValueError
    except ValueError:
        return _error('Choose a date in YYYY-MM-DD format.', fields={'date': ['Choose a valid date.']})
    now = timezone.now()
    start = datetime.combine(selected_date, time.min, tzinfo=RIDER_TIME_ZONE)
    zone = primary_delivery_zone()
    orders = list(Order.objects.filter(created_at__gte=start, created_at__lt=start + timedelta(days=1))
        .select_related('user', 'vendor', 'location', 'rider').prefetch_related('items__product',
            Prefetch('session_attempts', queryset=SessionOrder.objects.filter(status__in=['queued', 'delivered']),
                     to_attr='live_attempts')).order_by('pk'))
    data, vendors = [], {}
    for order in orders:
        attempt = next(iter(order.live_attempts), None)
        reason = _blocked_reason(order, zone, attempt)
        vendors[order.vendor_id] = _vendor(order.vendor)
        data.append({'id': order.pk, 'status': order.status, 'status_display': order.get_status_display(),
                     'vendor': vendors[order.vendor_id], 'location': _location(order.location),
                     'customer_name': order.user.name or 'Customer',
                     'total_amount': format(order.total_amount, '.2f'),
                     'delivery_fee': format(order.delivery_fee, '.2f'),
                     'items': [{'name': item.product.name if item.product else 'Deleted meal',
                                'quantity': item.quantity} for item in order.items.all()],
                     'session_id': attempt.session_id if attempt else None,
                     'selectable': not reason and selected_date == rider_local_date(),
                     'blocked_reason': reason or ("Choose today's date to create a session." if selected_date != rider_local_date() else ''),
                     'map_ready': _coordinates(order.location)})
    active_runs = dict(Session.objects.filter(status__in=['accepted', 'in_progress'], assigned_rider__isnull=False)
                       .values_list('assigned_rider_id', 'pk'))
    pending_offers = dict(JobOffer.objects.filter(status='pending', expires_at__gt=now, rider__isnull=False)
                         .values_list('rider_id', 'session_id'))
    riders = []
    for user in CustomUser.objects.filter(role='rider', is_active=True).select_related('rider_info').order_by('name', 'pk'):
        profile = getattr(user, 'rider_info', None)
        reason = ''
        if profile is None or not profile_completion(profile)['is_complete']:
            reason = 'Profile incomplete'
        elif user.pk in active_runs:
            reason = f'Running session #{active_runs[user.pk]}'
        elif user.pk in pending_offers:
            reason = f'Offer pending for session #{pending_offers[user.pk]}'
        riders.append({'id': user.pk, 'name': user.name or f'Rider #{user.pk}', 'phone': user.phone_number,
                       'eligible': not reason, 'reason': reason,
                       'acceptance_window': profile.job_acceptance_window if profile else 0})
    sessions = [_session_data(session, now) for session in _session_queryset().filter(date=selected_date).order_by('-pk')]
    return JsonResponse({'date': selected_date.isoformat(), 'today': rider_local_date().isoformat(),
                         'server_time': timezone.localtime(now, RIDER_TIME_ZONE).isoformat(),
                         'orders': data, 'vendors': sorted(vendors.values(), key=lambda vendor: vendor['name']),
                         'riders': riders, 'sessions': sessions,
                         'delivery_area': json.loads(zone.polygon.geojson) if zone and not zone.polygon.empty else None})


@require_POST
def create_session_api(request):
    if not is_platform_admin(request.user):
        return _error('Admin credentials required.', 403, 'permission_denied')
    try:
        payload = _request_json(request)
    except ValueError as exc:
        return _error(str(exc))
    data = SessionCreateSerializer(data=payload)
    if not data.is_valid():
        return _error('Check the highlighted session details.', fields=data.errors)
    values = dict(data.validated_data)
    selected_date = values.get('date') or rider_local_date()
    if selected_date != rider_local_date():
        return _error("Choose today's date to create a delivery session.", fields={'date': ["Use today's date."]})
    values['date'] = selected_date
    try:
        with transaction.atomic():
            orders = list(Order.objects.select_for_update(of=('self',)).filter(pk__in=values['orders'])
                          .select_related('vendor', 'location').prefetch_related(
                              Prefetch('session_attempts', queryset=SessionOrder.objects.filter(status__in=['queued', 'delivered']),
                                       to_attr='live_attempts')))
            zone = primary_delivery_zone()
            for order in orders:
                reason = _blocked_reason(order, zone, next(iter(order.live_attempts), None))
                if reason:
                    raise RiderActionError(f'Order #{order.pk}: {reason}. Refresh the map and select available orders.', 'order_unavailable')
                if order.created_at.astimezone(RIDER_TIME_ZONE).date() != selected_date:
                    raise RiderActionError('Select orders placed on the chosen delivery date.', 'wrong_order_date', 400)
            session = create_session(values.pop('vendor'), values.pop('orders'), **values)
    except RiderActionError as exc:
        return _error(str(exc), exc.status, exc.code)
    except ValidationError as exc:
        return _error('; '.join(exc.messages))
    except IntegrityError:
        return _error('An order or rider was reserved while you were planning. Refresh the map and try again.', 409, 'reservation_changed')
    return JsonResponse({'success': True, 'session': _session_data(_session_queryset().get(pk=session.pk), timezone.now())}, status=201)


@require_POST
def offer_session_api(request, session_id):
    if not is_platform_admin(request.user):
        return _error('Admin credentials required.', 403, 'permission_denied')
    try:
        payload = _request_json(request)
    except ValueError as exc:
        return _error(str(exc))
    class OfferInput(serializers.Serializer):
        rider_id = serializers.IntegerField(min_value=1, required=False)
        automatic = serializers.BooleanField(default=False)
    data = OfferInput(data=payload)
    if not data.is_valid():
        return _error('Choose a valid rider.', fields=data.errors)
    session_date = Session.objects.filter(pk=session_id).values_list('date', flat=True).first()
    if session_date is not None and session_date != rider_local_date():
        return _error("Only today's sessions can receive a new offer from this planner.")
    try:
        session, _ = offer_session(session_id, **data.validated_data)
    except RiderActionError as exc:
        return _error(str(exc), exc.status, exc.code)
    except ValidationError as exc:
        return _error('; '.join(exc.messages))
    except IntegrityError:
        return _error('This session or rider already has an offer. Refresh and try again.', 409, 'reservation_changed')
    return JsonResponse({'success': True, 'session': _session_data(_session_queryset().get(pk=session.pk), timezone.now())}, status=201)
