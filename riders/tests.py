from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from orders.models import Order
from orders.test_fixtures import OrderFixtures
from users.models import CustomUser
from .models import RiderInfo, Session, SessionOrder
from .timeutils import month_dates, period_bounds, week_dates


START = datetime(2026, 11, 1, 8, 0, tzinfo=datetime_timezone.utc)


class RiderFixtures(OrderFixtures):
    def make_session(self, **changes):
        data = {'assigned_rider': self.rider, 'vendor': self.vendor, 'date': START.date()}
        data.update(changes)
        return Session.objects.create(**data)

    def close_session(self, session, *, finished_at=None, confirmed=True, **changes):
        session.status = Session.Status.COMPLETED
        session.started_at = finished_at - timedelta(hours=1) if finished_at else START
        session.finished_at = finished_at or START + timedelta(hours=1)
        if confirmed:
            session.finance_status = Session.FinanceStatus.CONFIRMED
            session.settled_at = session.finished_at + timedelta(minutes=5)
        for field, value in changes.items():
            setattr(session, field, value)
        session.save()
        return session


class RiderProfileTests(RiderFixtures):
    def test_rider_profiles_are_created_once_and_customer_profiles_are_not_created(self):
        self.assertEqual(RiderInfo.objects.filter(user=self.rider).count(), 1)
        self.assertFalse(RiderInfo.objects.filter(user=self.customer).exists())
        self.rider.name = 'Updated rider'
        self.rider.save(update_fields=['name'])
        self.assertEqual(RiderInfo.objects.filter(user=self.rider).count(), 1)

    def test_becoming_a_rider_creates_a_profile_and_clears_a_cached_missing_relation(self):
        with self.assertRaises(RiderInfo.DoesNotExist):
            self.customer.rider_info
        self.customer.role = 'rider'
        self.customer.save(update_fields=['role'])
        self.assertEqual(self.customer.rider_info.user_id, self.customer.pk)

    def test_profile_rejects_a_non_rider_and_database_prevents_duplicate_profiles(self):
        with self.assertRaises(ValidationError):
            RiderInfo.objects.create(user=self.customer)
        with self.assertRaises(IntegrityError), transaction.atomic():
            RiderInfo.objects.bulk_create([RiderInfo(user=self.rider)])

    def test_an_empty_dashboard_returns_no_earnings_and_no_invented_performance(self):
        stats = self.rider.rider_info.stats()
        self.assertEqual(stats['rider_points'], 0)
        self.assertEqual(stats['rider_revenue'], Decimal('0.00'))
        self.assertEqual(stats['delivered_orders'], 0)
        self.assertEqual(stats['session_time'], timedelta(0))
        self.assertIsNone(stats['completion_rate'])
        self.assertIsNone(stats['on_time_rate'])
        self.assertIsNone(stats['average_speed_kmh'])


class SessionTests(RiderFixtures):
    def test_session_finances_and_time_are_derived_and_refresh_after_an_update(self):
        session = self.make_session(rider_cut=Decimal('100.10'), service_charge=Decimal('20.20'), fuel_surge_addition=Decimal('5.05'))
        self.assertEqual(session.rider_revenue, Decimal('105.15'))
        self.assertEqual(session.total_revenue, Decimal('125.35'))
        self.assertIsNone(session.session_time)
        session.rider_cut = Decimal('200.10')
        session.save(update_fields=['rider_cut'])
        self.assertEqual(session.total_revenue, Decimal('225.35'))
        self.close_session(session, confirmed=False)
        self.assertEqual(session.session_time, timedelta(hours=1))

    def test_sessions_reject_non_riders_and_inactive_riders(self):
        with self.assertRaises(ValidationError):
            self.make_session(assigned_rider=self.customer)
        self.other_rider.is_active = False
        self.other_rider.save(update_fields=['is_active'])
        with self.assertRaises(ValidationError):
            self.make_session(assigned_rider=self.other_rider)

    def test_riders_can_have_planned_runs_but_only_one_active_run(self):
        self.make_session()
        self.make_session(status=Session.Status.IN_PROGRESS, started_at=START)
        with self.assertRaises(ValidationError):
            self.make_session(status=Session.Status.IN_PROGRESS, started_at=START)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Session.objects.bulk_create([Session(
                assigned_rider=self.rider, rider_reference=self.rider.pk, date=START.date(),
                status=Session.Status.IN_PROGRESS, started_at=START,
            )])

    def test_negative_finances_invalid_times_and_premature_confirmation_are_rejected(self):
        for changes in [
            {'rider_cut': Decimal('-1.00')}, {'session_distance': Decimal('-0.001')},
            {'status': Session.Status.COMPLETED},
            {'started_at': START, 'finished_at': START - timedelta(minutes=1)},
            {'finance_status': Session.FinanceStatus.CONFIRMED, 'settled_at': START},
        ]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.make_session(**changes)

    def test_confirmed_session_facts_cannot_be_overwritten(self):
        session = self.close_session(self.make_session(), rider_cut=Decimal('100.00'))
        session.rider_cut = Decimal('200.00')
        with self.assertRaises(ValidationError):
            session.save()
        session.refresh_from_db()
        self.assertEqual(session.rider_revenue, Decimal('100.00'))

    def test_reassigning_a_planned_run_updates_its_reference_but_started_runs_keep_their_rider(self):
        session = self.make_session()
        session.assigned_rider = self.other_rider
        session.save(update_fields=['assigned_rider'])
        session.refresh_from_db()
        self.assertEqual(session.rider_reference, self.other_rider.pk)
        session.status = Session.Status.IN_PROGRESS
        session.started_at = START
        session.save()
        session.assigned_rider = self.rider
        with self.assertRaises(ValidationError):
            session.save()

    def test_a_run_cannot_close_with_queued_orders_or_before_a_delivery_finished(self):
        session = self.make_session()
        link = SessionOrder.objects.create(session=session, order=self.make_order(), assigned_at=START)
        with self.assertRaises(ValidationError):
            self.close_session(session, confirmed=False)
        link.status = SessionOrder.Status.DELIVERED
        link.finished_at = START + timedelta(hours=2)
        link.save()
        with self.assertRaises(ValidationError):
            self.close_session(session, confirmed=False)

    def test_deleting_a_rider_preserves_run_finances_and_its_rider_reference(self):
        session = self.make_session(status=Session.Status.IN_PROGRESS, started_at=START)
        reference = self.rider.pk
        self.rider.delete()
        session.refresh_from_db()
        self.assertIsNone(session.assigned_rider_id)
        self.assertEqual(session.rider_reference, reference)


class SessionOrderTests(RiderFixtures):
    def test_session_links_many_shared_orders_and_freezes_delivery_fees(self):
        session = self.make_session()
        order = self.make_order(delivery_fee=Decimal('30.00'))
        first = SessionOrder.objects.create(session=session, order=order, assigned_at=START)
        second = SessionOrder.objects.create(session=session, order=self.make_order(), assigned_at=START)
        self.assertEqual(set(session.orders.values_list('pk', flat=True)), {first.order_id, second.order_id})
        self.assertEqual(first.order.user_id, self.customer.pk)
        self.assertEqual(first.order.location_id, self.location.pk)
        Order.objects.filter(pk=order.pk).update(delivery_fee=Decimal('50.00'))
        first.refresh_from_db()
        self.assertEqual(first.delivery_fee, Decimal('30.00'))

    def test_an_order_cannot_be_in_two_live_runs_or_count_as_delivered_twice(self):
        order = self.make_order()
        session = self.make_session()
        link = SessionOrder.objects.create(session=session, order=order, assigned_at=START)
        other = self.make_session()
        with self.assertRaises(ValidationError):
            SessionOrder.objects.create(session=other, order=order, assigned_at=START)
        link.status = SessionOrder.Status.DELIVERED
        link.finished_at = START + timedelta(minutes=20)
        link.save()
        with self.assertRaises(ValidationError):
            SessionOrder.objects.create(session=other, order=order, status=SessionOrder.Status.DELIVERED,
                                        assigned_at=START, finished_at=START + timedelta(minutes=30))

    def test_failed_attempts_can_be_retried_in_another_run_and_cannot_be_reopened(self):
        order = self.make_order()
        failed = SessionOrder.objects.create(session=self.make_session(), order=order, assigned_at=START,
                                             status=SessionOrder.Status.FAILED, finished_at=START + timedelta(minutes=10),
                                             failure_reason='Customer unavailable')
        retry = SessionOrder.objects.create(session=self.make_session(), order=order, assigned_at=START + timedelta(minutes=20))
        self.assertEqual(retry.order_reference, failed.order_reference)
        failed.status = SessionOrder.Status.QUEUED
        failed.finished_at = None
        with self.assertRaises(ValidationError):
            failed.save()

    def test_wrong_rider_invalid_attempt_times_and_missing_failure_reasons_are_rejected(self):
        for changes in [
            {'order': self.make_order(rider=self.other_rider)},
            {'status': SessionOrder.Status.FAILED, 'finished_at': START + timedelta(minutes=10)},
            {'status': SessionOrder.Status.DELIVERED, 'finished_at': START - timedelta(seconds=1)},
            {'order': self.make_order(status='cancelled')},
        ]:
            data = {'session': self.make_session(), 'order': self.make_order(), 'assigned_at': START}
            data.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                SessionOrder.objects.create(**data)

    def test_attempt_references_and_fee_snapshots_are_immutable(self):
        link = SessionOrder.objects.create(session=self.make_session(), order=self.make_order(), assigned_at=START)
        link.delivery_fee = Decimal('1.00')
        with self.assertRaises(ValidationError):
            link.save()

    def test_deleting_the_source_order_keeps_completed_attempt_history(self):
        order = self.make_order()
        link = SessionOrder.objects.create(session=self.make_session(), order=order, assigned_at=START,
                                           status=SessionOrder.Status.DELIVERED, finished_at=START + timedelta(minutes=20))
        reference = order.pk
        order.delete()
        link.refresh_from_db()
        self.assertIsNone(link.order_id)
        self.assertEqual(link.order_reference, reference)
        self.assertEqual(link.delivery_fee, Decimal('30.00'))


class RiderStatisticsTests(RiderFixtures):
    def record_run(self, finished_at, *, delivered=1, failed=0, confirmed=True, **changes):
        session = self.make_session(**changes)
        for status, count in [(SessionOrder.Status.DELIVERED, delivered), (SessionOrder.Status.FAILED, failed)]:
            for _ in range(count):
                SessionOrder.objects.create(session=session, order=self.make_order(), status=status,
                    assigned_at=finished_at - timedelta(minutes=40), finished_at=finished_at - timedelta(minutes=5),
                    expected_delivery_at=finished_at, failure_reason='Not reachable' if status == SessionOrder.Status.FAILED else '')
        return self.close_session(session, finished_at=finished_at, confirmed=confirmed)

    def test_lifetime_dashboard_separates_rider_income_platform_share_and_performance(self):
        session = self.record_run(START + timedelta(hours=1), delivered=2, failed=1,
            rider_cut=Decimal('100.00'), service_charge=Decimal('20.00'), fuel_surge_addition=Decimal('10.00'),
            session_distance=Decimal('12.500'), points_awarded=5)
        stats = self.rider.rider_info.stats()
        self.assertEqual(stats['completed_sessions'], 1)
        self.assertEqual(stats['delivered_orders'], 2)
        self.assertEqual(stats['failed_orders'], 1)
        self.assertEqual(stats['completion_rate'], Decimal('66.67'))
        self.assertEqual(stats['on_time_rate'], Decimal('100.00'))
        self.assertEqual(stats['rider_revenue'], Decimal('110.00'))
        self.assertEqual(stats['total_revenue'], Decimal('130.00'))
        self.assertEqual(stats['service_charge'], Decimal('20.00'))
        self.assertEqual(stats['rider_points'], 5)
        self.assertEqual(stats['session_time'], timedelta(hours=1))
        self.assertEqual(stats['average_speed_kmh'], Decimal('12.50'))
        self.assertEqual(self.rider.rider_info.total_orders, 2)
        self.assertEqual(self.other_rider.rider_info.total_orders, 0)
        self.assertEqual(session.orders.count(), 3)

    def test_unconfirmed_runs_and_planned_runs_do_not_inflate_confirmed_revenue(self):
        self.record_run(START + timedelta(hours=1), confirmed=False, rider_cut=Decimal('100.00'))
        self.make_session(rider_cut=Decimal('999.00'), points_awarded=100)
        stats = self.rider.rider_info.stats()
        self.assertEqual(stats['rider_revenue'], Decimal('0.00'))
        self.assertEqual(stats['unconfirmed_revenue_sessions'], 1)
        self.assertEqual(stats['completed_sessions'], 1)
        self.assertEqual(stats['rider_points'], 0)

    def test_weekly_reports_use_monday_boundaries_in_sri_lankan_time(self):
        sunday = datetime(2026, 11, 1, 18, 20, tzinfo=datetime_timezone.utc)
        monday = datetime(2026, 11, 1, 18, 40, tzinfo=datetime_timezone.utc)
        self.record_run(sunday, points_awarded=2)
        self.record_run(monday, points_awarded=3)
        stats = self.rider.rider_info.weekly_stats(reference_date=datetime(2026, 11, 2).date())
        self.assertEqual(stats['completed_sessions'], 1)
        self.assertEqual(stats['rider_points'], 3)
        self.assertEqual(stats['start_date'], datetime(2026, 11, 2).date())
        self.assertEqual(stats['end_date'], datetime(2026, 11, 9).date())

    def test_monthly_reports_use_local_calendar_months_and_settlement_dates(self):
        october_end = datetime(2026, 10, 31, 18, 10, tzinfo=datetime_timezone.utc)
        november_start = datetime(2026, 10, 31, 18, 40, tzinfo=datetime_timezone.utc)
        old_run = self.record_run(october_end, confirmed=False, rider_cut=Decimal('100.00'))
        old_run.finance_status = Session.FinanceStatus.CONFIRMED
        old_run.settled_at = november_start
        old_run.save()
        self.record_run(november_start, rider_cut=Decimal('200.00'))
        stats = self.rider.rider_info.monthly_stats(reference_date=datetime(2026, 11, 10).date())
        self.assertEqual(stats['completed_sessions'], 1)
        self.assertEqual(stats['rider_revenue'], Decimal('300.00'))
        self.assertEqual(stats['start_date'], datetime(2026, 11, 1).date())
        self.assertEqual(stats['end_date'], datetime(2026, 12, 1).date())

    def test_calendar_helpers_handle_year_changes_and_invalid_periods(self):
        start, end = month_dates(datetime(2026, 12, 15).date())
        self.assertEqual(end, datetime(2027, 1, 1).date())
        self.assertEqual(week_dates(datetime(2026, 11, 8).date())[0], datetime(2026, 11, 2).date())
        with self.assertRaises(ValueError):
            period_bounds(start, start)
        with self.assertRaises(ValueError):
            period_bounds(start, None)


class RiderProfileMigrationTests(TransactionTestCase):
    def test_migration_backfills_existing_riders_without_duplicates(self):
        before = [('riders', '0001_initial'), ('users', '0003_ensure_user_schema')]
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(before)
            historical = executor.loader.project_state(before).apps
            User = historical.get_model('users', 'CustomUser')
            Profile = historical.get_model('riders', 'RiderInfo')
            rider = User.objects.create(phone_number='0776600001', role='rider')
            other = User.objects.create(phone_number='0776600002', role='rider')
            customer = User.objects.create(phone_number='0776600003', role='customer')
            Profile.objects.create(user_id=other.pk)
            MigrationExecutor(connection).migrate(latest)
            self.assertEqual(RiderInfo.objects.filter(user_id=rider.pk).count(), 1)
            self.assertEqual(RiderInfo.objects.filter(user_id=other.pk).count(), 1)
            self.assertFalse(RiderInfo.objects.filter(user_id=customer.pk).exists())
        finally:
            MigrationExecutor(connection).migrate(latest)
