"""Transactional job offers and single-kitchen delivery runs."""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Max, Q
from django.utils import timezone

from orders.models import Order
from orders.services.delivery_codes import delivery_code_matches
from vendors.models import Vendor
from .models import JobOffer, RiderCancellation, RiderInfo, RiderPenalty, Session, SessionOrder


class RiderActionError(ValueError):
    def __init__(self, detail, code='action_unavailable', status=409):
        super().__init__(detail)
        self.code, self.status = code, status


def _active_run(rider_id):
    return Session.objects.filter(assigned_rider_id=rider_id, status__in=['accepted', 'in_progress']).exists()


def _offer_next(session, now=None):
    now = now or timezone.now()
    if session.status != Session.Status.PLANNED or not session.dispatch_enabled or session.assigned_rider_id or not session.vendor_id:
        return None
    if session.dispatch_after and session.dispatch_after > now:
        return None
    if session.job_offers.filter(status=JobOffer.Status.PENDING).exists():
        return None
    excluded = session.job_offers.filter(offered_at__gte=now - timedelta(minutes=5)).values_list('rider_reference', flat=True)
    candidates = RiderInfo.objects.filter(user__role='rider', user__is_active=True).exclude(user_id__in=excluded)
    candidates = candidates.annotate(last_offer=Max('user__rider_job_offers__offered_at')).order_by(F('score').desc(nulls_last=True), F('last_offer').asc(nulls_first=True), 'pk')
    for profile_id in candidates.values_list('pk', flat=True):
        # Dispatch must not wait on profiles held by another session action.
        profile = RiderInfo.objects.select_for_update(of=('self',), skip_locked=True).select_related('user').filter(pk=profile_id).first()
        if profile is None:
            continue
        if not profile.user.is_active or profile.user.role != 'rider' or _active_run(profile.user_id):
            continue
        if JobOffer.objects.filter(rider_id=profile.user_id, status=JobOffer.Status.PENDING).exists():
            continue
        return JobOffer.objects.create(session=session, rider=profile.user, rider_reference=profile.user_id,
            acceptance_window=profile.job_acceptance_window, offered_at=now,
            expires_at=now + timedelta(seconds=profile.job_acceptance_window))
    return None


def dispatch_due(now=None, limit=100):
    """Used by the dispatcher worker and requests; deadlines do not depend on an open app."""
    now = now or timezone.now()
    expired_ids = list(JobOffer.objects.filter(status='pending', expires_at__lte=now).values_list('pk', flat=True)[:limit])
    changed = 0
    for offer_id in expired_ids:
        with transaction.atomic():
            session_id = JobOffer.objects.filter(pk=offer_id).values_list('session_id', flat=True).first()
            if session_id is None:
                continue
            session = Session.objects.select_for_update().filter(pk=session_id).first()
            if session is None:
                continue
            offer = JobOffer.objects.select_for_update().filter(pk=offer_id).first()
            if offer is None:
                continue
            if offer.status != 'pending' or offer.expires_at > now:
                continue
            offer.status, offer.responded_at = JobOffer.Status.EXPIRED, now
            offer.save(update_fields=['status', 'responded_at'])
            _offer_next(session, now)
            changed += 1
    session_ids = list(Session.objects.filter(status='planned', assigned_rider=None, dispatch_enabled=True, vendor__isnull=False)
        .filter(Q(dispatch_after=None) | Q(dispatch_after__lte=now)).exclude(job_offers__status='pending')
        .order_by('date', 'pk').values_list('pk', flat=True)[:limit])
    for session_id in session_ids:
        with transaction.atomic():
            session = Session.objects.select_for_update().filter(pk=session_id).first()
            if session is not None and _offer_next(session, now):
                changed += 1
    return changed


@transaction.atomic
def create_session(vendor_id, order_ids, *, rider_cut=Decimal('0.00'), service_charge=Decimal('0.00'),
                   fuel_surge_addition=Decimal('0.00'), estimated_minutes=None, estimated_distance=None,
                   dispatch_enabled=False, date=None, offer_to_rider=None):
    vendor = Vendor.objects.filter(pk=vendor_id).first()
    if vendor is None:
        raise RiderActionError('This kitchen no longer exists.', 'vendor_not_found', 404)
    if not vendor.is_active:
        raise RiderActionError('This kitchen is not available.', 'vendor_unavailable', 400)
    ids = list(set(order_ids))
    if not ids:
        raise RiderActionError('Choose at least one delivery order.', 'orders_required', 400)
    orders = list(Order.objects.select_for_update(of=('self',)).filter(pk__in=ids).select_related('location').order_by('pk'))
    if len(orders) != len(ids):
        raise RiderActionError('One of these orders no longer exists.', 'order_unavailable', 400)
    if any(order.vendor_id != vendor_id for order in orders):
        raise RiderActionError('Each session contains orders from one kitchen.', 'mixed_vendors', 400)
    if any(order.status in ['delivered', 'cancelled'] or order.location_id is None for order in orders):
        raise RiderActionError('Choose active orders with a delivery address.', 'invalid_order', 400)
    if SessionOrder.objects.filter(order_reference__in=ids, status__in=['queued', 'delivered']).exists():
        raise RiderActionError('An order already belongs to a delivery run.', 'order_already_assigned')
    fields = dict(vendor=vendor, rider_cut=rider_cut, service_charge=service_charge, fuel_surge_addition=fuel_surge_addition,
                  dispatch_enabled=dispatch_enabled, estimated_distance=estimated_distance)
    if date:
        fields['date'] = date
    if estimated_minutes is not None:
        fields['estimated_duration'] = timedelta(minutes=estimated_minutes)
    session = Session.objects.create(**fields)
    Order.objects.filter(pk__in=ids).update(rider=None)
    for order in orders:
        order.rider_id = None
        SessionOrder.objects.create(session=session, order=order)
    if offer_to_rider is not None:
        profile = RiderInfo.objects.select_for_update(of=('self',)).select_related('user').filter(
            user_id=offer_to_rider, user__role='rider', user__is_active=True).first()
        if profile is None:
            raise RiderActionError('Choose an active rider account.', 'rider_unavailable', 400)
        if _active_run(profile.user_id) or JobOffer.objects.filter(rider_id=profile.user_id, status='pending').exists():
            raise RiderActionError('This rider already has a session or a pending offer.', 'rider_busy')
        now = timezone.now()
        JobOffer.objects.create(session=session, rider=profile.user, rider_reference=profile.user_id,
            acceptance_window=profile.job_acceptance_window, offered_at=now,
            expires_at=now + timedelta(seconds=profile.job_acceptance_window))
    elif dispatch_enabled:
        _offer_next(session)
    return session


def _locked_offer(rider, offer_id):
    """All offer actions lock session, then offer, then rider profile."""
    session_id = JobOffer.objects.filter(pk=offer_id, rider=rider).values_list('session_id', flat=True).first()
    if session_id is None:
        raise RiderActionError('This offer is not available.', 'offer_not_found', 404)
    session = Session.objects.select_for_update().filter(pk=session_id).first()
    if session is None:
        raise RiderActionError('This offer is not available.', 'offer_not_found', 404)
    offer = JobOffer.objects.select_for_update().filter(pk=offer_id, rider=rider).first()
    if offer is None:
        raise RiderActionError('This offer is not available.', 'offer_not_found', 404)
    return session, offer


@transaction.atomic
def accept_offer(rider, offer_id):
    session, offer = _locked_offer(rider, offer_id)
    profile = RiderInfo.objects.select_for_update().get(user=rider)
    now = timezone.now()
    if offer.status != JobOffer.Status.PENDING or offer.expires_at <= now:
        raise RiderActionError('This offer has expired. Refresh Jobs for the next offer.', 'offer_expired')
    if session.status != Session.Status.PLANNED or session.assigned_rider_id or _active_run(rider.pk):
        raise RiderActionError('A delivery run is already active, or this job has been taken.', 'session_unavailable')
    offer.status, offer.responded_at = JobOffer.Status.ACCEPTED, now
    offer.save(update_fields=['status', 'responded_at'])
    session.assigned_rider, session.status, session.dispatch_enabled = rider, Session.Status.ACCEPTED, False
    ids = list(session.session_orders.filter(status='queued').values_list('order_reference', flat=True))
    Order.objects.filter(pk__in=ids).exclude(status__in=['delivered', 'cancelled']).update(rider=rider)
    session.save()
    JobOffer.objects.filter(rider=rider, status='pending').exclude(pk=offer.pk).update(status='withdrawn', responded_at=now)
    return session


@transaction.atomic
def decline_offer(rider, offer_id, reason=''):
    session, offer = _locked_offer(rider, offer_id)
    now = timezone.now()
    if offer.status != 'pending' or offer.expires_at <= now:
        raise RiderActionError('The offer has already closed.', 'offer_expired')
    offer.status, offer.responded_at, offer.response_reason = JobOffer.Status.DECLINED, now, reason
    offer.save(update_fields=['status', 'responded_at', 'response_reason'])
    _offer_next(session, now)
    return offer


def _own_session(rider, session_id):
    session = Session.objects.select_for_update().filter(pk=session_id, assigned_rider=rider).first()
    if session is None:
        raise RiderActionError('This session is not assigned to you.', 'session_not_found', 404)
    return session


@transaction.atomic
def start_session(rider, session_id):
    session = _own_session(rider, session_id)
    if session.status != Session.Status.ACCEPTED:
        raise RiderActionError('Accept the job before confirming pickup.', 'pickup_unavailable')
    now = timezone.now()
    session.status, session.started_at, session.pickup_confirmed_at = Session.Status.IN_PROGRESS, now, now
    ids = session.session_orders.filter(status='queued').values_list('order_reference', flat=True)
    Order.objects.filter(pk__in=ids).exclude(status__in=['delivered', 'cancelled']).update(status='out_for_delivery', rider=rider)
    session.save()
    return session


@transaction.atomic
def verify_delivery(rider, session_id, order_id, code, *, distance_km=None):
    session = _own_session(rider, session_id)
    if session.status != Session.Status.IN_PROGRESS:
        raise RiderActionError('Confirm pickup before verifying a delivery.', 'session_not_started')
    link = SessionOrder.objects.select_for_update().filter(session=session, order_reference=order_id).first()
    order = Order.objects.select_for_update().filter(pk=order_id, rider=rider).first()
    if link is None or order is None:
        raise RiderActionError('This order is not in your session.', 'order_not_found', 404)
    if link.status != SessionOrder.Status.QUEUED or order.status in ['delivered', 'cancelled']:
        raise RiderActionError('This delivery has already been resolved.', 'order_already_resolved')
    if not delivery_code_matches(order, code):
        raise RiderActionError('That PIN or QR code does not match this order. Ask the customer to check it.', 'invalid_delivery_code', 400)
    now = timezone.now()
    order.status = 'delivered'
    order.save(update_fields=['status'])
    link.status, link.finished_at = SessionOrder.Status.DELIVERED, now
    link.save(update_fields=['status', 'finished_at'])
    session.completed_orders_count = session.session_orders.filter(status='delivered').count()
    if distance_km is not None:
        session.session_distance = distance_km
    if not session.session_orders.filter(status='queued').exists():
        session.status, session.finished_at = Session.Status.COMPLETED, now
    session.save()
    return session


@transaction.atomic
def fail_delivery(rider, session_id, order_id, reason):
    session = _own_session(rider, session_id)
    if session.status != Session.Status.IN_PROGRESS:
        raise RiderActionError('Start the session before recording a failed delivery.', 'session_not_started')
    link = SessionOrder.objects.select_for_update().filter(session=session, order_reference=order_id, status='queued').first()
    if not link:
        raise RiderActionError('This order has already been resolved.', 'order_already_resolved')
    order = Order.objects.select_for_update().filter(pk=order_id, rider=rider).first()
    if order is None or order.status in ['delivered', 'cancelled']:
        raise RiderActionError('This order has already been resolved or reassigned.', 'order_already_resolved')
    link.status, link.finished_at, link.failure_reason = SessionOrder.Status.FAILED, timezone.now(), reason
    link.save()
    Order.objects.filter(pk=order.pk).update(rider=None, status='preparing')
    if not session.session_orders.filter(status='queued').exists():
        session.status, session.finished_at = Session.Status.COMPLETED, timezone.now()
    session.save()
    return session


@transaction.atomic
def cancel_session(rider, session_id, reason, details=''):
    session = _own_session(rider, session_id)
    profile = RiderInfo.objects.select_for_update().get(user=rider)
    if session.status not in [Session.Status.ACCEPTED, Session.Status.IN_PROGRESS]:
        raise RiderActionError('This session cannot be cancelled now.', 'cancellation_unavailable')
    stage, now = session.status, timezone.now()
    queued = list(session.session_orders.select_for_update(of=('self',)).filter(status='queued').select_related('order'))
    all_fee = sum((link.delivery_fee for link in session.session_orders.all()), Decimal('0.00'))
    remaining_fee = sum((link.delivery_fee for link in queued), Decimal('0.00'))
    ids = [link.order_reference for link in queued if link.order_id]
    for link in queued:
        link.status, link.finished_at = SessionOrder.Status.REMOVED, now
        link.save(update_fields=['status', 'finished_at'])
    Order.objects.filter(pk__in=ids).exclude(status__in=['delivered', 'cancelled']).update(rider=None, status='preparing')
    session.status, session.finished_at, session.dispatch_enabled = Session.Status.CANCELLED, now, False
    session.save()
    cancellation = RiderCancellation.objects.create(rider_info=profile, rider_reference=rider.pk,
        session=session, session_reference=session.pk, occurred_at=now, reason=reason, details=details,
        stage=stage, released_orders_count=len(ids))
    RiderInfo.objects.filter(pk=profile.pk).update(cancelled_sessions_count=F('cancelled_sessions_count') + 1, last_cancelled_at=now)
    if ids:
        ratio = remaining_fee / all_fee if all_fee > 0 else Decimal(len(ids)) / max(session.session_orders.count(), 1)
        remaining_cut = (session.rider_cut * ratio).quantize(Decimal('0.01'))
        remaining_service = (session.service_charge * ratio).quantize(Decimal('0.01'))
        remaining_fuel = (session.fuel_surge_addition * ratio).quantize(Decimal('0.01'))
        replacement = create_session(session.vendor_id, ids, rider_cut=remaining_cut, service_charge=remaining_service,
            fuel_surge_addition=remaining_fuel, estimated_distance=None, dispatch_enabled=False)
        JobOffer.objects.create(session=replacement, rider=rider, rider_reference=rider.pk, status='withdrawn',
            acceptance_window=profile.job_acceptance_window, offered_at=now, expires_at=now + timedelta(seconds=profile.job_acceptance_window),
            responded_at=now, response_reason='Previous rider cancelled this run')
        replacement.dispatch_enabled = True
        replacement.save(update_fields=['dispatch_enabled'])
        cancellation.replacement_session = replacement
        cancellation.save(update_fields=['replacement_session'])
        _offer_next(replacement, now)
    return cancellation


@transaction.atomic
def issue_penalty(rider_info, reference, reason, amount=Decimal('0.00'), points=0, cancellation=None):
    profile = RiderInfo.objects.select_for_update().get(pk=rider_info.pk)
    existing = RiderPenalty.objects.filter(reference=reference).first()
    if existing:
        if existing.rider_reference != profile.user_id:
            raise RiderActionError('This penalty reference is already in use.', 'penalty_reference_conflict')
        return existing
    penalty = RiderPenalty(rider_info=profile, rider_reference=profile.user_id, reference=reference,
                           reason=reason, amount=amount, points=points, cancellation=cancellation)
    penalty.full_clean(validate_unique=False)
    penalty, created = RiderPenalty.objects.get_or_create(reference=reference, defaults={
        'rider_info': profile, 'rider_reference': profile.user_id, 'reason': reason,
        'amount': amount, 'points': points, 'cancellation': cancellation,
    })
    if not created:
        if penalty.rider_reference != profile.user_id:
            raise RiderActionError('This penalty reference is already in use.', 'penalty_reference_conflict')
        return penalty
    RiderInfo.objects.filter(pk=profile.pk).update(total_penalties_count=F('total_penalties_count') + 1,
        last_penalty_at=penalty.issued_at, penalty_balance=F('penalty_balance') + amount, penalty_points=F('penalty_points') + points)
    return penalty
