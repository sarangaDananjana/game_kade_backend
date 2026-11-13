from unittest.mock import Mock, patch
from datetime import datetime, timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo
import requests
from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from .models import CustomUser, OTP
from .utils import normalize_mobile_number, send_otp_sms, SmsDeliveryUncertain
from core.security import security_settings


class LoginPhoneTests(SimpleTestCase):
    def test_supported_formats_are_canonical(self):
        for value in ['0721992125', '721992125', '+94 72 199 2125', '94721992125', '0094721992125']:
            self.assertEqual(normalize_mobile_number(value), '0721992125')
        for value in ['123456789', '07219', 'phone', True, [], None]:
            with self.assertRaises(ValueError):
                normalize_mobile_number(value)


class SmsTransportTests(SimpleTestCase):
    @patch('users.utils.requests.post')
    def test_test_numbers_never_reach_the_sms_provider(self, post):
        for number in ['0721992125', '0774610536', '94721992125', '+94774610536']:
            self.assertTrue(send_otp_sms(number, '123456'))
        post.assert_not_called()

    @patch.dict('os.environ', {'TEXT_LK_API_KEY': 'isolated-test-key'})
    @patch('users.utils.requests.post')
    def test_a_provider_error_in_http_200_is_not_reported_as_sent(self, post):
        post.return_value=Mock(status_code=200)
        post.return_value.json.return_value={'status':'error', 'message':'Rejected'}
        self.assertFalse(send_otp_sms('0771000001', '123456'))

    @patch.dict('os.environ', {'TEXT_LK_API_KEY': 'isolated-test-key'})
    @patch('users.utils.requests.post')
    def test_success_uses_international_recipient_format(self, post):
        post.return_value=Mock(status_code=201)
        post.return_value.json.return_value={'status':'success'}
        self.assertTrue(send_otp_sms('0771000001', '123456'))
        self.assertEqual(post.call_args.kwargs['json']['recipient'], '94771000001')

    @patch.dict('os.environ', {'TEXT_LK_API_KEY': 'isolated-test-key'})
    @patch('users.utils.requests.post')
    def test_a_lost_response_does_not_claim_the_sms_was_rejected(self, post):
        for error in [requests.exceptions.ReadTimeout(), requests.exceptions.ConnectionError()]:
            with self.subTest(error=type(error).__name__):
                post.side_effect = error
                with self.assertRaises(SmsDeliveryUncertain):
                    send_otp_sms('0771000001', '123456')
        self.assertEqual(post.call_count, 2)  # Never retry a paid send automatically.

    @patch.dict('os.environ', {'TEXT_LK_API_KEY': 'isolated-test-key'})
    @patch('users.utils.requests.post')
    def test_an_unreadable_success_response_keeps_delivery_uncertain(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.side_effect = ValueError('invalid JSON')
        with self.assertRaises(SmsDeliveryUncertain):
            send_otp_sms('0771000001', '123456')

    @patch.dict('os.environ', {'TEXT_LK_API_KEY': 'isolated-test-key'})
    @patch('users.utils.requests.post')
    def test_a_connection_timeout_before_sending_is_a_failure(self, post):
        post.side_effect = requests.exceptions.ConnectTimeout()
        self.assertFalse(send_otp_sms('0771000001', '123456'))

    @patch.dict('os.environ', {'TEXT_LK_API_KEY': 'isolated-test-key'})
    @patch('users.utils.requests.post')
    def test_http_rejections_are_failures(self, post):
        for status_code in [301, 401, 422, 500]:
            with self.subTest(status_code=status_code):
                post.return_value = Mock(status_code=status_code)
                self.assertFalse(send_otp_sms('0771000001', '123456'))


class LoginOtpTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rider=CustomUser.objects.create_user('0721992125', name='Saranga', role='rider')
        cls.customer=CustomUser.objects.create_user('0774610536', name='Saranga', role='customer')

    def setUp(self):
        self.client=APIClient()

    @patch('users.views.send_otp_sms')
    def test_both_test_accounts_save_random_codes_without_sms(self, sms):
        for user in [self.rider, self.customer]:
            response=self.client.post('/api/auth/send-otp/', {'phone_number':user.phone_number,'role':user.role},format='json')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['delivery_method'], 'admin')
            self.assertEqual(response.data['otp_length'], 6)
            otp=OTP.objects.get(user=user)
            self.assertEqual(len(otp.otp_code), 6)
            self.assertTrue(otp.is_valid())
            self.assertNotIn('otp_code', response.data)
        sms.assert_not_called()

    @patch('users.views.send_otp_sms')
    def test_nine_digit_login_and_verification_use_the_existing_rider(self, sms):
        response=self.client.post('/api/auth/send-otp/',{'phone_number':'721992125','role':'rider'},format='json')
        self.assertEqual(response.status_code,200)
        otp=OTP.objects.get(user=self.rider)
        verified=self.client.post('/api/auth/verify-otp/',{'phone_number':'+94721992125','otp_code':otp.otp_code},format='json')
        self.assertEqual(verified.status_code,200)
        self.assertEqual(verified.data['role'],'rider')
        self.assertIn('access',verified.data)
        self.assertEqual(CustomUser.objects.count(),2)
        sms.assert_not_called()

    @patch('users.views.send_otp_sms')
    def test_a_stale_authorization_header_does_not_block_login(self, sms):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer invalid-token')
        response=self.client.post('/api/auth/send-otp/',{'phone_number':'0721992125','role':'rider'},format='json')
        self.assertEqual(response.status_code,200)
        sms.assert_not_called()

    @patch('users.views.send_otp_sms')
    def test_customer_role_is_not_changed_by_a_rider_login_request(self, sms):
        self.client.post('/api/auth/send-otp/',{'phone_number':'0774610536','role':'rider'},format='json')
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.role,'customer')
        sms.assert_not_called()

    @patch('users.views.send_otp_sms')
    def test_invalid_phone_does_not_create_an_account_or_send_sms(self, sms):
        response=self.client.post('/api/auth/send-otp/',{'phone_number':'1234'},format='json')
        self.assertEqual(response.status_code,400)
        self.assertEqual(CustomUser.objects.count(),2)
        sms.assert_not_called()

    @patch('users.views.send_otp_sms', return_value=False)
    def test_regular_numbers_still_attempt_sms_and_report_failure(self, sms):
        user=CustomUser.objects.create_user('0771000001',name='Rider',role='rider')
        response=self.client.post('/api/auth/send-otp/',{'phone_number':user.phone_number,'role':'rider'},format='json')
        self.assertEqual(response.status_code,500)
        sms.assert_called_once()

    @override_settings(**{**security_settings({'RATE_LIMIT_REDIS_URL': 'redis://unused:6379/0'}, debug=False), 'RATE_LIMIT_ENABLED': False})
    @patch('users.utils.requests.post')
    def test_production_defaults_keep_both_accounts_admin_only(self, post):
        for user, number in [(self.customer, '+94 77 461 0536'), (self.rider, '0094721992125')]:
            with self.subTest(number=number):
                sent = self.client.post('/api/auth/send-otp/', {'phone_number': number}, format='json', secure=True)
                self.assertEqual(sent.status_code, 200)
                self.assertEqual(sent.data['delivery_method'], 'admin')
                otp = OTP.objects.get(user=user)
                self.assertEqual(datetime.fromisoformat(sent.data['expires_at']), otp.expires_at)
                self.assertTrue(0 < sent.data['expires_in_seconds'] <= 300)
                verified = self.client.post('/api/auth/verify-otp/', {'phone_number': number, 'otp_code': otp.otp_code}, format='json', secure=True)
                self.assertEqual(verified.status_code, 200)
        post.assert_not_called()

    @patch('users.views.send_otp_sms', side_effect=SmsDeliveryUncertain)
    def test_code_can_be_verified_after_the_sms_response_is_lost(self, sms):
        user = CustomUser.objects.create_user('0771000001', name='Customer')
        sent = self.client.post('/api/auth/send-otp/', {'phone_number': user.phone_number}, format='json')
        self.assertEqual(sent.status_code, 202)
        self.assertTrue(sent.data['delivery_pending'])
        self.assertEqual(sent.data['delivery_method'], 'sms')
        otp = OTP.objects.get(user=user)
        self.assertTrue(otp.is_valid())
        verified = self.client.post('/api/auth/verify-otp/', {'phone_number': user.phone_number, 'otp_code': otp.otp_code}, format='json')
        self.assertEqual(verified.status_code, 200)
        sms.assert_called_once()

    def test_expired_code_reports_expiry_without_counting_a_wrong_guess(self):
        otp = OTP.objects.create(user=self.customer, otp_code='123456')
        OTP.objects.filter(pk=otp.pk).update(created_at=timezone.now() - timedelta(minutes=6))
        verified = self.client.post('/api/auth/verify-otp/', {'phone_number': self.customer.phone_number, 'otp_code': otp.otp_code}, format='json')
        self.assertEqual(verified.status_code, 400)
        self.assertEqual(verified.data['code'], 'otp_expired')
        otp.refresh_from_db()
        self.assertEqual(otp.failed_attempts, 0)

    def test_five_digit_input_explains_the_required_length(self):
        otp = OTP.objects.create(user=self.customer, otp_code='123456')
        verified = self.client.post('/api/auth/verify-otp/', {'phone_number': self.customer.phone_number, 'otp_code': '12345'}, format='json')
        self.assertEqual(verified.status_code, 400)
        self.assertEqual(str(verified.data['otp_code'][0]), 'Enter the six-digit OTP.')
        otp.refresh_from_db()
        self.assertEqual(otp.failed_attempts, 0)

    def test_admin_lists_latest_first_and_does_not_label_an_older_code_active(self):
        admin = CustomUser.objects.create_superuser('0771000099', password='local-test-only', name='Admin')
        older = OTP.objects.create(user=self.customer, otp_code='111111')
        latest = OTP.objects.create(user=self.customer, otp_code='222222', is_used=True)
        self.client.force_login(admin)
        response = self.client.get('/admin/users/otp/')
        self.assertEqual(response.status_code, 200)
        rows = list(response.context['cl'].result_list)
        self.assertEqual(rows[0].pk, latest.pk)
        model_admin = response.context['cl'].model_admin
        self.assertEqual(model_admin.code_status(next(row for row in rows if row.pk == older.pk)), 'Replaced')
        self.assertEqual(model_admin.code_status(rows[0]), 'Used / replaced')
        self.assertContains(response, 'Expires at')


class OtpExpiryTests(SimpleTestCase):
    def test_utc_and_colombo_timestamps_have_the_same_five_minute_lifetime(self):
        created = datetime(2026, 11, 13, 7, 30, tzinfo=datetime_timezone.utc)
        for setting_zone in ['UTC', 'Asia/Colombo']:
            for stored_zone in [datetime_timezone.utc, ZoneInfo('Asia/Colombo')]:
                with self.subTest(setting_zone=setting_zone, stored_zone=stored_zone), override_settings(TIME_ZONE=setting_zone):
                    otp = OTP(created_at=created.astimezone(stored_zone), otp_code='123456')
                    with patch('users.models.timezone.now', return_value=created + timedelta(minutes=4, seconds=59)):
                        self.assertTrue(otp.is_valid())
                    with patch('users.models.timezone.now', return_value=created + timedelta(minutes=5, microseconds=1)):
                        self.assertFalse(otp.is_valid())
