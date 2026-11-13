from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.utils import timezone
from .models import RiderInfo, RiderTopUp, RiderWalletEntry
from .services import RiderActionError

ACCEPTANCE_PERCENT = Decimal('20')


def acceptance_fee(session):
    return ((session.rider_cut + session.fuel_surge_addition) * ACCEPTANCE_PERCENT / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def charge_acceptance(profile, session, offer):
    """Caller holds session, offer and profile locks inside one transaction."""
    fee = acceptance_fee(session)
    if profile.topup_balance < fee:
        raise RiderActionError(f'Top up Rs. {fee - profile.topup_balance:.2f} more to accept this job. The 20% fee is Rs. {fee:.2f}.', 'insufficient_topup_balance', 409)
    profile.topup_balance -= fee
    profile.save(update_fields=['topup_balance', 'updated_at'])
    RiderWalletEntry.objects.create(rider=profile, kind='job_fee', amount=-fee, balance_after=profile.topup_balance,
        offer=offer, session_reference=session.pk)


@transaction.atomic
def review_topup(pk, staff, approve, note=''):
    # All funding operations lock the profile before the payment request.
    rider_id = RiderTopUp.objects.filter(pk=pk).values_list('rider_id', flat=True).get()
    profile = RiderInfo.objects.select_for_update().get(pk=rider_id)
    topup = RiderTopUp.objects.select_for_update().get(pk=pk)
    if topup.status != 'pending':
        return topup
    if approve:
        balance = profile.topup_balance + topup.amount
        if balance > Decimal('9999999999.99'):
            raise RiderActionError('The resulting balance exceeds the wallet limit.', 'balance_limit', 400)
        profile.topup_balance = balance
        profile.save(update_fields=['topup_balance', 'updated_at'])
        RiderWalletEntry.objects.create(rider=profile, kind='topup', amount=topup.amount, balance_after=balance, topup=topup)
    topup.status = 'approved' if approve else 'rejected'
    topup.reviewed_by, topup.reviewed_at, topup.staff_note = staff, timezone.now(), note[:250]
    topup.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'staff_note'])
    return topup
