"""Idempotent development records for one rider; dummy catalog/accounts stay inactive."""

import hashlib
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from orders.models import Order, OrderItem, OrderLocation
from products.models import Product
from users.models import CustomUser
from vendors.models import Vendor
from riders.models import JobOffer, RiderCancellation, RiderInfo, Session, SessionOrder
from riders.services import issue_penalty
from riders.timeutils import RIDER_TIME_ZONE


class Command(BaseCommand):
    help = 'Add labelled dummy history and a private job for a development rider account.'

    def add_arguments(self, parser):
        parser.add_argument('--phone', required=True)
        parser.add_argument('--refresh-offer', action='store_true', help='Renew only this demo job after its previous deadline.')

    def handle(self, *args, **options):
        phone = options['phone']
        if not (phone.isdigit() and len(phone) == 10 and phone.startswith('0')):
            raise CommandError('Use the rider account\'s 10-digit local phone number.')
        rider = CustomUser.objects.filter(phone_number=phone, role='rider', is_active=True).first()
        if rider is None:
            raise CommandError('Create an active rider account for this phone number first.')
        tag = f'MEAL_RIDER_DEMO:{phone}'
        now = timezone.now()
        base = 100000 + int(hashlib.sha256(tag.encode()).hexdigest()[:8], 16) % 700000
        created_runs = 0
        with transaction.atomic():
            profile = RiderInfo.objects.select_for_update().get(user=rider)
            owner = self.dummy_user(f'000{phone[-4:]}001', f'[Demo] Kitchen owner {phone[-4:]}', 'owner')
            vendor, _ = Vendor.objects.get_or_create(owner=owner, shop_name=f'[Demo] Meal Rider kitchen {phone[-4:]}',
                defaults={'lat': 5.94042, 'lng': 80.57983, 'district': 'Matara', 'is_active': False})
            if vendor.is_active:
                raise CommandError('The demo kitchen must stay inactive in the customer catalog.')
            product, _ = Product.objects.get_or_create(vendor=vendor, name='[Demo] Chicken rice and curry',
                defaults={'price': Decimal('380.00'), 'category': 'rice_curry', 'stock_quantity': 0, 'is_available': False})
            locations = []
            for index, (label, lat, lng) in enumerate([
                ('Ruhuna Campus gate', 5.93970, 80.57460), ('Ruhuna Campus gate', 5.93970, 80.57460),
                ('Meddewatta blue gate', 5.94651, 80.57318), ('Hospital staff entrance', 5.95027, 80.55080),
                ('Hospital staff entrance', 5.95027, 80.55080),
            ]):
                customer = self.dummy_user(f'000{phone[-4:]}1{index:02d}', f'[Demo] Customer {index+1} {phone[-4:]}', 'customer')
                location, _ = OrderLocation.objects.get_or_create(user=customer, unique_identity=f'Demo gate {phone[-4:]} {index}',
                    defaults={'name': f'[Demo] {label}', 'lat': lat, 'lng': lng, 'district': 'Matara',
                              'description': 'Development delivery address; no real customer.'})
                locations.append(location)
            cuts = ['420.00', '350.00', '460.00', '390.00', '520.00', '480.00', '300.00']
            offsets = [0, 1, 2, 3, 6, 10, 18]
            for index, (days, cut) in enumerate(zip(offsets, cuts)):
                notes = f'{tag}:completed:{index}'
                if Session.objects.filter(notes=notes).exists():
                    continue
                finish = now - timedelta(days=days, minutes=5)
                start = finish - timedelta(minutes=32)
                session = self.new_session(vendor, notes, finish, rider, Decimal(cut))
                for sequence, location in enumerate(locations):
                    at = start + timedelta(minutes=(sequence+1)*6)
                    order = self.new_order(vendor, product, location, rider, 'delivered', start-timedelta(minutes=10), base+index*10+sequence, session)
                    SessionOrder.objects.create(session=session, order=order, status='delivered', sequence=sequence+1,
                        assigned_at=start-timedelta(minutes=10), finished_at=at,
                        expected_delivery_at=at+timedelta(minutes=3) if sequence<4 else at-timedelta(minutes=2))
                session.status, session.started_at, session.finished_at = 'completed', start, finish
                session.completed_orders_count, session.points_awarded = 5, 12
                session.session_distance = Decimal('5.400') + Decimal(index)/10
                session.finance_status, session.settled_at = 'confirmed', finish+timedelta(minutes=2)
                session.save()
                created_runs += 1
            for index, days in enumerate([3, 8]):
                notes = f'{tag}:cancelled:{index}'
                session = Session.objects.filter(notes=notes).first()
                at = now-timedelta(days=days, hours=1)
                if session is None:
                    session = self.new_session(vendor, notes, at, rider, Decimal('250.00'))
                    for sequence, location in enumerate(locations[:2]):
                        order = self.new_order(vendor, product, location, None, 'cancelled', at-timedelta(minutes=20), base+80+index*10+sequence, session)
                        SessionOrder.objects.create(session=session, order=order, status='removed', sequence=sequence+1,
                            assigned_at=at-timedelta(minutes=20), finished_at=at)
                    session.status, session.finished_at = 'cancelled', at
                    if index == 1:
                        session.started_at = at-timedelta(minutes=15)
                    session.save()
                    created_runs += 1
                event, created = RiderCancellation.objects.get_or_create(session_reference=session.pk, rider_reference=rider.pk,
                    defaults={'rider_info': profile, 'session': session, 'occurred_at': at,
                              'reason': 'vehicle_issue', 'details': '[Demo] Practice cancellation record; no real delivery.',
                              'stage': 'accepted' if index == 0 else 'in_progress', 'released_orders_count': 2})
                if created:
                    latest = max(filter(None, [profile.last_cancelled_at, event.occurred_at]))
                    RiderInfo.objects.filter(pk=profile.pk).update(cancelled_sessions_count=F('cancelled_sessions_count')+1, last_cancelled_at=latest)
                    profile.last_cancelled_at = latest
            issue_penalty(profile, f'{tag}:penalty:1', '[Demo] Cancellation review', Decimal('25.00'), 1)
            issue_penalty(profile, f'{tag}:penalty:2', '[Demo] Delivery review', Decimal('50.00'), 2)
            if profile.score is None:
                profile.score, profile.score_version, profile.score_updated_at = Decimal('92.40'), 'demo-v1', now
                profile.job_acceptance_window = 300
                profile.save(update_fields=['score', 'score_version', 'score_updated_at', 'job_acceptance_window', 'updated_at'])
            job = Session.objects.filter(notes=f'{tag}:job').first()
            if job is None:
                job = self.new_session(vendor, f'{tag}:job', now, None, Decimal('420.00'))
                for sequence, location in enumerate(locations):
                    order = self.new_order(vendor, product, location, None, 'preparing', now, base+110+sequence, job)
                    SessionOrder.objects.create(session=job, order=order, sequence=sequence+1)

        # Match the session -> offer -> profile lock order used by acceptance/expiry.
        with transaction.atomic():
            job = Session.objects.select_for_update().get(pk=job.pk)
            pending = job.job_offers.select_for_update().filter(status='pending').first()
            profile = RiderInfo.objects.select_for_update().get(user=rider)
            if options['refresh_offer'] and pending and pending.expires_at <= timezone.now():
                pending.status, pending.responded_at = 'expired', timezone.now()
                pending.save(update_fields=['status', 'responded_at'])
                pending = None
            if job.status == 'planned' and pending is None and (options['refresh_offer'] or not job.job_offers.exists()):
                busy = Session.objects.filter(assigned_rider=rider, status__in=['accepted', 'in_progress']).exists()
                if not busy and not JobOffer.objects.filter(rider=rider, status='pending').exists():
                    at = timezone.now()
                    pending = JobOffer.objects.create(session=job, rider=rider, rider_reference=rider.pk,
                        acceptance_window=profile.job_acceptance_window, offered_at=at,
                        expires_at=at+timedelta(seconds=profile.job_acceptance_window))
            # dispatch_enabled remains False: this private sample never passes to other riders.
        self.stdout.write(f'Rider {phone}: {created_runs} new history sessions; demo job {job.pk}; demo PIN 4821.')
        self.stdout.write(f'Profile completion: name/vehicle/emergency details preserved. Private offer: {pending.pk if pending else "none"}.')

    def dummy_user(self, phone, name, role):
        user = CustomUser.objects.filter(phone_number=phone).first()
        if user is not None:
            if user.name != name or user.role != role or user.is_active:
                raise CommandError('A reserved demo phone conflicts with an existing account.')
            return user
        return CustomUser.objects.create_user(phone_number=phone, name=name, role=role, is_active=False)

    def new_session(self, vendor, notes, at, rider, cut):
        return Session.objects.create(vendor=vendor, assigned_rider=rider, notes=notes,
            date=at.astimezone(RIDER_TIME_ZONE).date(), rider_cut=cut, service_charge=Decimal('80.00'),
            fuel_surge_addition=Decimal('35.00'), estimated_distance=Decimal('5.400'), estimated_duration=timedelta(minutes=32))

    def new_order(self, vendor, product, location, rider, status, created_at, prefix, session):
        code = f'{prefix:06d}4821'
        while Order.objects.filter(delivery_code=code).exists():
            prefix += 200
            code = f'{prefix:06d}4821'
        fee = (session.rider_cut+session.service_charge+session.fuel_surge_addition)/5
        order = Order.objects.create(user=location.user, vendor=vendor, location=location, rider=rider, status=status,
            created_at=created_at, total_amount=product.price+fee, delivery_fee=fee, delivery_code=code)
        OrderItem.objects.create(order=order, product=product, quantity=1, price_at_purchase=product.price)
        return order
