from io import StringIO

from django.core.management import call_command

from orders.test_fixtures import OrderFixtures
from .models import JobOffer, Session
from .profile import profile_completion


class RiderDemoSeedTests(OrderFixtures):
    def test_demo_seed_is_private_idempotent_and_keeps_personal_details(self):
        self.rider.name = 'Saranga'
        self.rider.save(update_fields=['name'])
        phone = self.rider.phone_number
        call_command('seed_rider_demo', phone=phone, stdout=StringIO())
        profile = self.rider.rider_info
        profile.refresh_from_db()
        self.assertEqual(profile_completion(profile)['remaining_tasks'], 0)
        self.assertEqual(profile.cancelled_sessions_count, 2)
        self.assertEqual(profile.total_penalties_count, 2)
        self.assertEqual(profile.stats()['delivered_orders'], 35)
        self.assertEqual(profile.stats()['completed_sessions'], 7)
        self.assertFalse(profile.is_available)
        sessions = Session.objects.filter(notes__startswith=f'MEAL_RIDER_DEMO:{phone}')
        self.assertEqual(sessions.count(), 10)
        job = sessions.get(notes__endswith=':job')
        self.assertFalse(job.dispatch_enabled)
        self.assertFalse(job.vendor.is_active)
        self.assertEqual(job.job_offers.get(status='pending').rider_id, self.rider.pk)
        self.assertTrue(all(link.order.delivery_pin == '4821' for link in job.session_orders.all()))
        call_command('seed_rider_demo', phone=phone, stdout=StringIO())
        profile.refresh_from_db()
        self.assertEqual(sessions.count(), 10)
        self.assertEqual(profile.cancelled_sessions_count, 2)
        self.assertEqual(profile.total_penalties_count, 2)
        self.assertEqual(JobOffer.objects.filter(session=job).count(), 1)
