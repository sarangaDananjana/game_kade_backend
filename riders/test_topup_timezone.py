import uuid
from datetime import datetime, timezone as datetime_timezone
from decimal import Decimal
from unittest.mock import patch
from django.test import Client, SimpleTestCase
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from orders.test_fixtures import OrderFixtures
from core.timeutils import SriLankaTimezoneMiddleware, sri_lanka_isoformat
from .models import RiderInfo, RiderTopUp, RiderWalletEntry, Session


class SriLankaTimeTests(SimpleTestCase):
    def test_utc_timestamp_formats_as_the_requested_colombo_instant(self):
        self.assertEqual(sri_lanka_isoformat(datetime(2026,11,13,16,30,tzinfo=datetime_timezone.utc)), '2026-11-13T22:00:00+05:30')

    def test_request_timezone_is_colombo_and_outer_timezone_is_restored(self):
        with timezone.override('UTC'):
            middleware=SriLankaTimezoneMiddleware(lambda request: HttpResponse(timezone.get_current_timezone_name()))
            self.assertEqual(middleware(None).content,b'Asia/Colombo')
            self.assertEqual(timezone.get_current_timezone_name(),'UTC')


class RiderTopUpPageTests(OrderFixtures):
    def setUp(self):
        self.owner.role='admin';self.owner.save(update_fields=['role'])
        self.client=Client();self.client.force_login(self.owner)
        self.url=reverse('dispatch-rider-topup')

    def payment(self, **changes):
        values={'rider':self.rider.pk,'amount':'100.00','payment_reference':'cash-123',
            'staff_note':'Collected cash','request_id':str(uuid.uuid4())}
        values.update(changes);return values

    def test_dashboard_links_rider_to_prefilled_topup_form(self):
        response=self.client.get(reverse('dispatch-dashboard'))
        self.assertContains(response,'Top up a rider')
        self.assertContains(response,self.url)
        response=self.client.get(self.url,{'rider':self.rider.pk})
        self.assertEqual(response.status_code,200)
        self.assertEqual(str(response.context['form']['rider'].value()),str(self.rider.pk))
        self.assertContains(response,'Credit rider balance')

    def test_role_admin_can_credit_collected_money_and_retry_does_not_duplicate(self):
        self.assertFalse(self.owner.is_staff)
        values=self.payment()
        first=self.client.post(self.url,values);second=self.client.post(self.url,values)
        self.assertEqual(first.status_code,302);self.assertEqual(second.status_code,302)
        self.assertEqual(RiderInfo.objects.get(user=self.rider).topup_balance,Decimal('100.00'))
        self.assertEqual(RiderWalletEntry.objects.count(),1)
        self.assertEqual(RiderTopUp.objects.get().reviewed_by,self.owner)

    def test_invalid_amount_and_over_limit_leave_no_payment_or_credit(self):
        response=self.client.post(self.url,self.payment(amount='-10.00'))
        self.assertEqual(response.status_code,200);self.assertTrue(response.context['form'].errors)
        RiderInfo.objects.filter(user=self.rider).update(topup_balance=Decimal('9999999999.99'))
        response=self.client.post(self.url,self.payment())
        self.assertContains(response,'exceeds the wallet limit')
        self.assertFalse(RiderTopUp.objects.exists());self.assertFalse(RiderWalletEntry.objects.exists())

    def test_non_admin_and_missing_csrf_cannot_credit_funds(self):
        self.client.force_login(self.customer)
        self.assertEqual(self.client.post(self.url,self.payment()).status_code,302)
        csrf_client=Client(enforce_csrf_checks=True);csrf_client.force_login(self.owner)
        self.assertEqual(csrf_client.post(self.url,self.payment()).status_code,403)
        self.assertFalse(RiderWalletEntry.objects.exists())

    def test_api_and_admin_show_colombo_times_even_with_an_outer_utc_override(self):
        end=datetime(2026,11,13,15,0,tzinfo=datetime_timezone.utc)
        settlement=datetime(2026,11,13,16,30,tzinfo=datetime_timezone.utc)
        session=Session.objects.create(vendor=self.vendor,assigned_rider=self.rider,status='completed',
            started_at=datetime(2026,11,13,14,0,tzinfo=datetime_timezone.utc),finished_at=end,
            finance_status='confirmed',settled_at=settlement)
        api=APIClient();api.force_authenticate(self.rider)
        with timezone.override('UTC'):
            response=api.get(f'/api/riders/sessions/{session.pk}/')
            self.assertEqual(response.data['settled_at'],'2026-11-13T22:00:00+05:30')
            self.assertTrue(response.data['finished_at'].endswith('+05:30'))
            self.owner.is_staff=self.owner.is_superuser=True;self.owner.save(update_fields=['is_staff','is_superuser'])
            response=self.client.get(reverse('admin:riders_session_change',args=[session.pk]))
            self.assertContains(response,'10 p.m.')

    def test_dispatch_rider_list_includes_available_balance(self):
        response=self.client.get(reverse('dispatch-orders'))
        rider=next(r for r in response.json()['riders'] if r['id']==self.rider.pk)
        self.assertEqual(Decimal(rider['topup_balance']),Decimal('0.00'))
