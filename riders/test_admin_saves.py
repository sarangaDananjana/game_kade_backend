"""Exercise real admin POSTs, including failures after form validation."""

from datetime import timedelta
from decimal import Decimal
import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

from django.contrib import admin
from django.db import IntegrityError, connection
from django.forms import MultiWidget
from django.test import Client, SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from orders.test_fixtures import OrderFixtures
from .models import RiderInfo, RiderTopUp, RiderWalletEntry, Session, SessionOrder


def isolated_production_settings(**overrides):
    environment = {
        'DATABASE_URL': 'postgresql://test:test@localhost:6543/test_only',
        'SECRET_KEY': 'test-production-settings-key-1234567890-only-for-isolated-verification',
        'RATE_LIMIT_REDIS_URL': 'redis://localhost:6379/0',
        **overrides,
    }
    path = Path(__file__).resolve().parents[1] / 'food_delivery' / 'settings.py'
    spec = importlib.util.spec_from_file_location('isolated_admin_production_settings', path)
    module = importlib.util.module_from_spec(spec)
    # Import settings as data only: no .env files, real credentials, connections
    # or changes to the running test application's Django settings.
    with patch.dict(os.environ, environment, clear=True), patch('dotenv.load_dotenv'):
        spec.loader.exec_module(module)
    return module


class RiderAdminConfigurationTests(SimpleTestCase):
    def test_transaction_pooler_uses_client_side_cursors_by_default(self):
        settings = isolated_production_settings()
        self.assertFalse(settings.DEBUG)
        self.assertTrue(settings.DATABASES['default']['DISABLE_SERVER_SIDE_CURSORS'])
        self.assertTrue(settings.DATABASES['default']['CONN_HEALTH_CHECKS'])

    def test_direct_database_can_explicitly_enable_streaming_cursors(self):
        settings = isolated_production_settings(DATABASE_DISABLE_SERVER_SIDE_CURSORS='False')
        self.assertFalse(settings.DATABASES['default']['DISABLE_SERVER_SIDE_CURSORS'])


class RiderAdminSaveTests(OrderFixtures):
    def setUp(self):
        self.owner.is_staff = self.owner.is_superuser = True
        self.owner.save(update_fields=['is_staff', 'is_superuser'])
        self.client = Client()
        self.client.force_login(self.owner)

    def session(self, **changes):
        values = {'vendor': self.vendor, 'assigned_rider': self.rider}
        values.update(changes)
        return Session.objects.create(**values)

    def form_data(self, form):
        data = {}
        for name, field in form.fields.items():
            key = form.add_prefix(name)
            value = form[name].value()
            if isinstance(field.widget, MultiWidget):
                values = field.widget.decompress(value)
                for index, widget in enumerate(field.widget.widgets):
                    data[f'{key}_{index}'] = widget.format_value(values[index]) or ''
            else:
                data[key] = field.widget.format_value(value) or ''
        return data

    def session_form(self, session):
        url = reverse('admin:riders_session_change', args=[session.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = self.form_data(response.context['adminform'].form)
        for inline in response.context['inline_admin_formsets']:
            data.update(self.form_data(inline.formset.management_form))
            for form in inline.formset:
                data.update(self.form_data(form))
        data['_save'] = 'Save'
        return url, data

    def test_confirmed_finances_are_readonly_and_notes_can_save_without_changing_timestamps(self):
        end = timezone.now() - timedelta(hours=1)
        session = self.session(status='completed', started_at=end - timedelta(hours=1),
                               finished_at=end, finance_status='confirmed', settled_at=end)
        url, data = self.session_form(session)
        data['notes'] = 'Confirmed run checked'
        data['rider_cut'] = '200.00'
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.rider_cut, Decimal('0.00'))
        self.assertEqual(session.finished_at, end)
        self.assertEqual(session.notes, 'Confirmed run checked')
        response = self.client.get(url)
        self.assertNotIn('rider_cut', response.context['adminform'].form.fields)
        self.assertNotIn('finished_at', response.context['adminform'].form.fields)

    def test_started_session_cannot_change_riders(self):
        session = self.session(status='in_progress', started_at=timezone.now())
        url, data = self.session_form(session)
        data['assigned_rider'] = self.other_rider.pk
        response = self.client.post(url, data)
        self.assertContains(response, 'A started session keeps its original rider.')
        session.refresh_from_db()
        self.assertEqual(session.assigned_rider_id, self.rider.pk)
        data['assigned_rider'] = self.rider.pk
        self.assertEqual(self.client.post(url, data).status_code, 302)

    def test_admin_foreign_key_choices_do_not_use_named_cursors_with_pooler_settings(self):
        session = self.session()
        database = isolated_production_settings().DATABASES['default']
        with patch.dict(connection.settings_dict,
                        {'DISABLE_SERVER_SIDE_CURSORS': database['DISABLE_SERVER_SIDE_CURSORS']}):
            with patch.object(connection, 'chunked_cursor', side_effect=AssertionError('Named cursor used')):
                response = self.client.get(reverse('admin:riders_session_change', args=[session.pk]))
        self.assertContains(response, self.vendor.shop_name)

    def test_inline_failure_rolls_back_parent_changes(self):
        session = self.session()
        order = self.make_order()
        link = SessionOrder.objects.create(session=session, order=order, status='delivered',
            assigned_at=timezone.now() - timedelta(hours=1), finished_at=timezone.now())
        url, data = self.session_form(session)
        data['notes'] = 'Must roll back with the invalid delivery edit'
        data['session_orders-0-order'] = self.make_order().pk
        response = self.client.post(url, data)
        self.assertContains(response, 'An attempt keeps its original session')
        session.refresh_from_db()
        link.refresh_from_db()
        self.assertEqual(session.notes, '')
        self.assertEqual(link.order_id, order.pk)
        self.assertEqual(response.context['adminform'].form['notes'].value(), data['notes'])

    def test_duplicate_inline_order_shows_an_error_without_saving(self):
        order = self.make_order()
        SessionOrder.objects.create(session=self.session(), order=order)
        session = self.session()
        url, data = self.session_form(session)
        data.update({'session_orders-TOTAL_FORMS': '1', 'session_orders-0-order': order.pk,
            'session_orders-0-status': 'queued', 'session_orders-0-sequence': '0',
            'session_orders-0-assigned_at_0': timezone.localdate().isoformat(),
            'session_orders-0-assigned_at_1': '00:00:00'})
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['adminform'].form.non_field_errors())
        self.assertFalse(session.session_orders.exists())

    def test_topup_limit_error_does_not_create_payment_or_wallet_entry(self):
        RiderInfo.objects.filter(user=self.rider).update(topup_balance=Decimal('9999999999.99'))
        response = self.client.post(reverse('admin:riders_ridertopup_add'), {
            'rider': self.rider.rider_info.pk, 'amount': '1.00',
            'payment_reference': 'limit-receipt', 'staff_note': '', '_save': 'Save'})
        self.assertContains(response, 'The resulting balance exceeds the wallet limit.')
        self.assertFalse(RiderTopUp.objects.exists())
        self.assertFalse(RiderWalletEntry.objects.exists())

    def test_constraint_conflict_rolls_back_save_and_returns_a_form_error(self):
        session = self.session()
        url, data = self.session_form(session)
        data['notes'] = 'Must roll back if the audit write conflicts'
        with patch.object(admin.site._registry[Session], 'log_change', side_effect=IntegrityError('conflict')):
            with self.assertLogs('riders.admin', level='WARNING'):
                response = self.client.post(url, data)
        self.assertContains(response, 'This change conflicts with another saved record.')
        session.refresh_from_db()
        self.assertEqual(session.notes, '')

    def test_row_constraint_conflict_rolls_back_session_and_order_updates(self):
        from .services import create_session
        order = self.make_order(rider=None)
        session = create_session(self.vendor.pk, [order.pk])
        with patch.object(admin.site._registry[Session], 'log_change', side_effect=IntegrityError('conflict')):
            with self.assertLogs('riders.admin', level='WARNING'):
                response = self.client.post(reverse('admin:riders_session_update_row', args=[session.pk]),
                    {'rider_id': self.rider.pk, 'status': 'accepted', 'finance_status': 'draft'})
        self.assertEqual(response.status_code, 302)
        session.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(session.status, 'planned')
        self.assertIsNone(order.rider_id)

    def test_bulk_confirmation_skips_invalid_session_and_confirms_valid_session(self):
        now = timezone.now()
        invalid = self.session(status='completed', started_at=now,
                               finished_at=now + timedelta(hours=1))
        valid = self.session(status='completed', assigned_rider=self.other_rider,
                             started_at=now - timedelta(hours=2), finished_at=now - timedelta(hours=1))
        response = self.client.post(reverse('admin:riders_session_changelist'), {
            'action': 'confirm_finances', '_selected_action': [invalid.pk, valid.pk]})
        self.assertEqual(response.status_code, 302)
        invalid.refresh_from_db()
        valid.refresh_from_db()
        self.assertEqual(invalid.finance_status, 'draft')
        self.assertEqual(valid.finance_status, 'confirmed')
