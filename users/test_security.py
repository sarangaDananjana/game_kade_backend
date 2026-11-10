from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from core.rate_limits import local_store
from core.security import DEFAULT_RATE_LIMITS
from .models import CustomUser, OTP


@override_settings(RATE_LIMIT_ENABLED=True, OTP_SMS_BYPASS_NUMBERS=set())
class AuthenticationGuardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = CustomUser.objects.create_user('0771000001', name='Customer')

    def setUp(self):
        self.client = APIClient()
        local_store.clear()
        # Do not let tests straddle real minute/hour boundaries.
        clock = patch('core.rate_limits.time.time', return_value=120010)
        clock.start()
        self.addCleanup(clock.stop)

    def verify(self, code, *, phone='0771000001', ip='198.51.100.5'):
        return self.client.post('/api/auth/verify-otp/', {'phone_number': phone, 'otp_code': code},
                                format='json', REMOTE_ADDR=ip)

    @patch('users.views.send_otp_sms', return_value=True)
    def test_resend_budget_uses_normalized_phone_across_ips(self, sms):
        first = self.client.post('/api/auth/send-otp/', {'phone_number': '0771000001'}, format='json')
        second = self.client.post('/api/auth/send-otp/', {'phone_number': '+94 77 100 0001'},
                                  format='json', REMOTE_ADDR='198.51.100.7')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertIn('Retry-After', second)
        self.assertEqual(OTP.objects.filter(user=self.user).count(), 1)
        sms.assert_called_once()

    @patch('users.views.send_otp_sms', return_value=True)
    def test_two_step_signup_finishes_without_sms_cooldown(self, sms):
        first = self.client.post('/api/auth/send-otp/', {'phone_number': '0771000002'}, format='json')
        second = self.client.post('/api/auth/send-otp/', {'phone_number': '0771000002', 'name': 'New customer'}, format='json')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(OTP.objects.filter(user__phone_number='0771000002').count(), 1)
        sms.assert_called_once()

    @patch('users.views.send_otp_sms', return_value=True)
    def test_sending_from_one_ip_is_limited_before_account_creation_or_sms(self, sms):
        for index in range(5):
            response = self.client.post('/api/auth/send-otp/',
                {'phone_number': f'077200000{index}', 'name': 'Customer'}, format='json')
            self.assertEqual(response.status_code, 200)
        before = CustomUser.objects.count()
        blocked = self.client.post('/api/auth/send-otp/', {'phone_number': '0772000009', 'name': 'Customer'}, format='json')
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(CustomUser.objects.count(), before)
        self.assertEqual(sms.call_count, 5)

    @override_settings(RATE_LIMITS={**DEFAULT_RATE_LIMITS, 'otp_send_global': ('2/minute',)})
    @patch('users.views.send_otp_sms', return_value=True)
    def test_global_sms_budget_holds_across_phones_and_ips(self, sms):
        for index in range(2):
            response = self.client.post('/api/auth/send-otp/',
                {'phone_number': f'077300000{index}', 'name': 'Customer'}, format='json', REMOTE_ADDR=f'198.51.100.{index + 1}')
            self.assertEqual(response.status_code, 200)
        blocked = self.client.post('/api/auth/send-otp/', {'phone_number': '0773000009', 'name': 'Customer'},
                                   format='json', REMOTE_ADDR='198.51.100.50')
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(sms.call_count, 2)

    def test_five_wrong_guesses_lock_the_code_even_when_ips_change(self):
        otp = OTP.objects.create(user=self.user, otp_code='123456')
        for index in range(5):
            self.assertEqual(self.verify('999999', ip=f'198.51.100.{index + 1}').status_code, 400)
        self.assertEqual(self.verify('123456', phone='+94771000001', ip='198.51.100.50').status_code, 400)
        otp.refresh_from_db()
        self.assertEqual(otp.failed_attempts, 5)
        self.assertTrue(otp.is_used)

    def test_successful_code_cannot_be_replayed(self):
        OTP.objects.create(user=self.user, otp_code='123456')
        success = self.verify('123456')
        self.assertEqual(success.status_code, 200)
        self.assertIn('access', success.data)
        self.assertEqual(self.verify('123456').status_code, 400)

    def test_used_latest_code_does_not_reveal_an_older_unused_code(self):
        OTP.objects.create(user=self.user, otp_code='111111')
        OTP.objects.create(user=self.user, otp_code='222222', is_used=True)
        self.assertEqual(self.verify('111111').status_code, 400)

    @override_settings(RATE_LIMITS={**DEFAULT_RATE_LIMITS, 'otp_send_phone': ('2/minute',)})
    @patch('users.views.send_otp_sms', return_value=True)
    @patch('users.views.generate_otp_code', side_effect=['111111', '222222'])
    def test_resend_invalidates_the_previous_code(self, generate, sms):
        for _ in range(2):
            self.assertEqual(self.client.post('/api/auth/send-otp/', {'phone_number': self.user.phone_number}, format='json').status_code, 200)
        self.assertEqual(self.verify('111111').status_code, 400)
        self.assertEqual(self.verify('222222').status_code, 200)

    @patch('users.views.send_otp_sms', return_value=False)
    def test_failed_sms_leaves_no_usable_code(self, sms):
        sent = self.client.post('/api/auth/send-otp/', {'phone_number': self.user.phone_number}, format='json')
        self.assertEqual(sent.status_code, 500)
        otp = OTP.objects.get(user=self.user)
        self.assertTrue(otp.is_used)
        self.assertEqual(self.verify(otp.otp_code).status_code, 400)

    @patch('users.views.send_otp_sms', return_value=True)
    def test_fixed_test_account_receives_a_real_code_in_production_mode(self, sms):
        sent = self.client.post('/api/auth/send-otp/', {'phone_number': '0700000000', 'name': 'Customer'}, format='json')
        self.assertEqual(sent.status_code, 200)
        self.assertEqual(sent.data['delivery_method'], 'sms')
        sms.assert_called_once()
        self.assertEqual(self.verify('00000', phone='0700000000').status_code, 400)
        self.assertEqual(len(OTP.objects.get(user__phone_number='0700000000').otp_code), 6)

    @patch('users.views.send_otp_sms')
    def test_disabled_account_cannot_send_or_verify_a_code(self, sms):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        OTP.objects.create(user=self.user, otp_code='123456')
        self.assertEqual(self.client.post('/api/auth/send-otp/', {'phone_number': self.user.phone_number}, format='json').status_code, 400)
        self.assertEqual(self.verify('123456').status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        sms.assert_not_called()

    def test_unknown_and_incorrect_accounts_receive_the_same_verification_error(self):
        OTP.objects.create(user=self.user, otp_code='123456')
        wrong = self.verify('999999')
        unknown = self.verify('999999', phone='0779999999')
        self.assertEqual(wrong.status_code, unknown.status_code)
        self.assertEqual(wrong.data, unknown.data)

    def test_rotated_refresh_token_cannot_be_reused(self):
        refresh = str(RefreshToken.for_user(self.user))
        first = self.client.post('/api/auth/token/refresh/', {'refresh': refresh}, format='json')
        self.assertEqual(first.status_code, 200)
        self.assertIn('refresh', first.data)
        self.assertNotEqual(first.data['refresh'], refresh)
        self.assertEqual(self.client.post('/api/auth/token/refresh/', {'refresh': refresh}, format='json').status_code, 401)
        self.assertEqual(self.client.post('/api/auth/token/refresh/', {'refresh': first.data['refresh']}, format='json').status_code, 200)

    @override_settings(RATE_LIMITS={**DEFAULT_RATE_LIMITS, 'api_user': ('2/minute',)})
    def test_authenticated_user_budget_survives_changing_ips(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(self.user).access_token}')
        for index in range(2):
            self.assertEqual(self.client.get('/api/auth/profile/', REMOTE_ADDR=f'198.51.100.{index + 1}').status_code, 200)
        self.assertEqual(self.client.get('/api/auth/profile/', REMOTE_ADDR='198.51.100.50').status_code, 429)

    @override_settings(RATE_LIMITS={**DEFAULT_RATE_LIMITS, 'delivery_quote': ('2/minute',)})
    def test_delivery_quote_aliases_share_a_budget(self):
        for path in ['/api/orders/delivery-quote/', '/shop/orders/delivery-quote/']:
            self.assertEqual(self.client.post(path, {}, format='json').status_code, 400)
        self.assertEqual(self.client.post('/api/orders/delivery-quote/', {}, format='json').status_code, 429)

    @override_settings(RATE_LIMITS={**DEFAULT_RATE_LIMITS, 'checkout': ('2/minute',)})
    def test_checkout_aliases_share_a_budget(self):
        self.client.force_authenticate(self.user)
        for path in ['/api/orders/create/', '/shop/orders/create/']:
            self.assertEqual(self.client.post(path, {}, format='json').status_code, 400)
        self.assertEqual(self.client.post('/api/orders/', {}, format='json').status_code, 429)

    @override_settings(RATE_LIMITS={**DEFAULT_RATE_LIMITS, 'token_refresh_ip': ('2/minute',)})
    def test_token_refresh_endpoint_limits_invalid_attempts(self):
        for _ in range(2):
            self.assertEqual(self.client.post('/api/auth/token/refresh/', {'refresh': 'invalid'}, format='json').status_code, 401)
        self.assertEqual(self.client.post('/api/auth/token/refresh/', {'refresh': 'invalid'}, format='json').status_code, 429)

    @patch('requests.get')
    def test_paid_route_aliases_share_the_riders_budget(self, get):
        rider = CustomUser.objects.create_user('0771000003', name='Rider', role='rider')
        self.client.force_authenticate(rider)
        for path in ['/api/orders/routes/optimize/', '/api/orders/rider/routes/optimize/', '/shop/rider/routes/optimize/']:
            self.assertEqual(self.client.post(path, {'lat': 6.9, 'lng': 79.8}, format='json').status_code, 404)
        self.assertEqual(self.client.post('/api/orders/rider/routes/optimize/', {'lat': 6.9, 'lng': 79.8}, format='json').status_code, 429)
        get.assert_not_called()

    @patch('requests.get')
    def test_untrusted_map_coordinates_are_rejected_before_external_calls(self, get):
        rider = CustomUser.objects.create_user('0771000003', name='Rider', role='rider')
        self.client.force_authenticate(rider)
        response = self.client.post('/api/orders/rider/routes/optimize/',
                                    {'lat': 'https://attacker.example', 'lng': 80}, format='json')
        self.assertEqual(response.status_code, 400)
        get.assert_not_called()

    @patch('users.views.send_otp_sms')
    def test_malformed_login_payload_cannot_create_an_account(self, sms):
        for payload in [{'phone_number': ['0771000009']}, {'phone_number': '0771000009', 'name': 'x' * 256},
                        {'phone_number': '0771000009', 'role': 'admin'}]:
            self.assertEqual(self.client.post('/api/auth/send-otp/', payload, format='json').status_code, 400)
        self.assertFalse(CustomUser.objects.filter(phone_number='0771000009').exists())
        sms.assert_not_called()
