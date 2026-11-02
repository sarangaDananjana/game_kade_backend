"""Admin row changes use the same delivery lifecycle as rider actions."""

from django.db import transaction
from django.utils import timezone

from orders.models import Order
from .models import JobOffer, RiderInfo, Session
from .profile import profile_completion
from .services import RiderActionError, cancel_session, start_session


@transaction.atomic
def update_session_from_admin(session_id, *, rider_id, status, finance_status):
    session = Session.objects.select_for_update().get(pk=session_id)
    if status not in Session.Status.values or finance_status not in Session.FinanceStatus.values:
        raise RiderActionError('Choose a valid session and financial status.', status=400)
    if session.finance_status == Session.FinanceStatus.CONFIRMED:
        if (status, finance_status, rider_id) != (session.status, session.finance_status, session.assigned_rider_id):
            raise RiderActionError('Confirmed session facts are locked. Record an adjustment separately.')
        return session
    if session.status in [Session.Status.COMPLETED, Session.Status.CANCELLED]:
        if status != session.status or rider_id != session.assigned_rider_id:
            raise RiderActionError('Closed sessions keep their status and rider.')
    elif session.started_at and rider_id != session.assigned_rider_id:
        raise RiderActionError('A started session keeps its original rider.')
    elif rider_id != session.assigned_rider_id or status == Session.Status.ACCEPTED:
        if rider_id is None and status != Session.Status.PLANNED:
            raise RiderActionError('Choose a rider before accepting or starting a session.', status=400)
        if rider_id is not None:
            profile = RiderInfo.objects.select_for_update().select_related('user').filter(
                user_id=rider_id, user__role='rider', user__is_active=True).first()
            if profile is None or not profile_completion(profile)['is_complete']:
                raise RiderActionError('Choose an active rider with a complete profile.', 'profile_incomplete', 400)
            if Session.objects.filter(assigned_rider_id=rider_id, status__in=['accepted', 'in_progress']).exclude(pk=session.pk).exists():
                raise RiderActionError('This rider already has an active session.')
            if JobOffer.objects.filter(rider_id=rider_id, status='pending').exclude(session=session).exists():
                raise RiderActionError('This rider already has a pending offer.')
        ids = list(session.session_orders.filter(status='queued').values_list('order_reference', flat=True))
        Order.objects.filter(pk__in=ids).exclude(status__in=['delivered', 'cancelled']).update(rider_id=rider_id)
        session.assigned_rider_id = rider_id
        session.save()
        session.job_offers.filter(status='pending').update(status='withdrawn', responded_at=timezone.now())

    transitions = {'planned': ['planned', 'accepted', 'cancelled'], 'accepted': ['accepted', 'in_progress', 'cancelled'],
                   'in_progress': ['in_progress', 'completed', 'cancelled'], 'completed': ['completed'], 'cancelled': ['cancelled']}
    if status not in transitions[session.status]:
        raise RiderActionError('Move a session forward through accepted, in progress, and completed. Closed sessions cannot reopen.')
    if status != session.status:
        if status == Session.Status.IN_PROGRESS:
            session = start_session(session.assigned_rider, session.pk)
        elif status == Session.Status.CANCELLED:
            if session.status in [Session.Status.ACCEPTED, Session.Status.IN_PROGRESS]:
                cancel_session(session.assigned_rider, session.pk, 'other', 'Cancelled from the admin session list.')
                session.refresh_from_db()
            else:
                now = timezone.now()
                links = list(session.session_orders.select_for_update().filter(status='queued'))
                for link in links:
                    link.status, link.finished_at = 'removed', now
                    link.save(update_fields=['status', 'finished_at'])
                Order.objects.filter(pk__in=[link.order_reference for link in links]).exclude(status__in=['delivered', 'cancelled']).update(rider=None, status='preparing')
                session.status, session.finished_at, session.dispatch_enabled = status, now, False
                session.job_offers.filter(status='pending').update(status='withdrawn', responded_at=now)
                session.save()
        else:
            if status == Session.Status.COMPLETED:
                if session.session_orders.filter(status='queued').exists():
                    raise RiderActionError('Verify or resolve every order before completing the session. Customer verification is required.')
                session.finished_at = timezone.now()
                session.completed_orders_count = session.session_orders.filter(status='delivered').count()
            session.status, session.dispatch_enabled = status, False
            session.save()
    if finance_status == Session.FinanceStatus.CONFIRMED:
        if session.status != Session.Status.COMPLETED:
            raise RiderActionError('Complete the session before confirming its finances.')
        session.finance_status, session.settled_at = finance_status, timezone.now()
        session.save()
    return session
