from unittest.mock import Mock, patch
from django.test import TestCase, SimpleTestCase, override_settings
from rest_framework.test import APIClient
from .models import CustomUser, OTP
from .utils import normalize_mobile_number, send_otp_sms


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
