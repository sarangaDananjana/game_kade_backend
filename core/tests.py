"""Request guards tested through Django, plus optional real Redis concurrency."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import JsonResponse
from django.test import SimpleTestCase, override_settings
from django.urls import path
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from .rate_limits import RateLimitExceeded, consume_limits, local_store
from .security import DEFAULT_RATE_LIMITS, client_address, security_settings, signing_key


view_calls = Mock()


def probe(request):
    view_calls()
    return JsonResponse({'ok': True, 'file_sizes': [file.size for file in request.FILES.values()]})


class PublicAPIView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return JsonResponse({'ok': True})


urlpatterns = [
    path('probe/', probe),
    path('api/', PublicAPIView.as_view()),
    path('admin/login/', probe),
    path('dispatch/login/', probe),
    path('shop/admin/login/', probe),
]


class SecurityConfigurationTests(SimpleTestCase):
    def test_production_requires_a_private_signing_key_and_redis(self):
        for secret in ['', 'change-me', 'a' * 64, 'django-insecure-' + 'abc123' * 20]:
            with self.assertRaises(ImproperlyConfigured):
                signing_key({'SECRET_KEY': secret}, debug=False)
        secret = 'private-test-key-' + uuid.uuid4().hex + uuid.uuid4().hex
        self.assertEqual(signing_key({'SECRET_KEY': secret}, debug=False), secret)
        with self.assertRaises(ImproperlyConfigured):
            security_settings({}, debug=False)
        config = security_settings({'RATE_LIMIT_REDIS_URL': 'redis://private:6379/0'}, debug=False)
        self.assertFalse(config['RATE_LIMIT_USE_LOCAL'])
        self.assertEqual(config['OTP_SMS_BYPASS_NUMBERS'], {'0774610536', '0721992125'})
        self.assertTrue(config['SESSION_COOKIE_SECURE'])
        self.assertTrue(config['SECURE_SSL_REDIRECT'])

    def test_bad_configuration_does_not_silently_disable_protection(self):
        for overrides in [
            {'RATE_LIMIT_IP': ''}, {'RATE_LIMIT_IP': '0/minute'}, {'RATE_LIMIT_IP': '10/week'},
            {'TRUSTED_PROXY_CIDRS': 'anything'}, {'MAX_REQUEST_BODY_BYTES': '-1'},
            {'OTP_MAX_FAILED_ATTEMPTS': '100'}, {'SECURE_SSL_REDIRECT': 'maybe'},
            {'OTP_SMS_BYPASS_NUMBERS': '12345'},
        ]:
            with self.subTest(overrides=overrides), self.assertRaises(ImproperlyConfigured):
                security_settings(overrides, debug=True)

    def test_sms_bypass_configuration_normalizes_international_numbers(self):
        config = security_settings({'OTP_SMS_BYPASS_NUMBERS': '+94 77 461 0536,0094721992125'}, debug=True)
        self.assertEqual(config['OTP_SMS_BYPASS_NUMBERS'], {'0774610536', '0721992125'})

    def test_empty_or_extra_bypass_configuration_cannot_send_sms_to_owner_accounts(self):
        for value in ['', ' ', '0771000001']:
            with self.subTest(value=value):
                config = security_settings({'OTP_SMS_BYPASS_NUMBERS': value}, debug=True)
                self.assertTrue({'0774610536', '0721992125'} <= config['OTP_SMS_BYPASS_NUMBERS'])
                if value.strip():
                    self.assertIn('0771000001', config['OTP_SMS_BYPASS_NUMBERS'])

    def test_untrusted_peer_cannot_choose_its_ip(self):
        address, trusted = client_address({
            'REMOTE_ADDR': '198.51.100.5', 'HTTP_X_FORWARDED_FOR': '203.0.113.9',
        }, ['127.0.0.1/32'])
        self.assertEqual(address, '198.51.100.5')
        self.assertFalse(trusted)

    def test_trusted_proxy_uses_the_rightmost_untrusted_hop(self):
        address, trusted = client_address({
            'REMOTE_ADDR': '127.0.0.1',
            'HTTP_X_FORWARDED_FOR': '203.0.113.9, 198.51.100.5, 10.0.0.2',
        }, ['127.0.0.1/32', '10.0.0.0/24'])
        self.assertEqual(address, '198.51.100.5')
        self.assertTrue(trusted)

    def test_malformed_chain_cannot_supply_a_new_identity(self):
        for forwarded in ['invalid', '203.0.113.9, invalid', 'x' * 2000]:
            self.assertEqual(client_address({
                'REMOTE_ADDR': '127.0.0.1', 'HTTP_X_FORWARDED_FOR': forwarded,
            }, ['127.0.0.1/32']), ('127.0.0.1', True))


@override_settings(RATE_LIMIT_ENABLED=True, ROOT_URLCONF=__name__)
class RequestGuardTests(SimpleTestCase):
    def setUp(self):
        local_store.clear()
        view_calls.reset_mock()

    @override_settings(RATE_LIMITS={**DEFAULT_RATE_LIMITS, 'ip': ('2/minute',)})
    @patch('core.rate_limits.time.time', return_value=120010)
    def test_excess_requests_stop_before_the_view_and_include_retry_time(self, now):
        self.assertEqual(self.client.get('/probe/').status_code, 200)
        self.assertEqual(self.client.get('/probe/').status_code, 200)
        blocked = self.client.get('/probe/')
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json()['code'], 'rate_limited')
        self.assertEqual(blocked['Retry-After'], '50')
        self.assertEqual(blocked['Cache-Control'], 'no-store')
        self.assertEqual(view_calls.call_count, 2)

    @override_settings(RATE_LIMITS={**DEFAULT_RATE_LIMITS, 'ip': ('1/minute',)})
    def test_forged_headers_do_not_reset_the_socket_peers_budget(self):
        first = self.client.get('/probe/', REMOTE_ADDR='198.51.100.5', HTTP_X_FORWARDED_FOR='203.0.113.8')
        second = self.client.get('/probe/', REMOTE_ADDR='198.51.100.5', HTTP_X_FORWARDED_FOR='203.0.113.9')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)

    @override_settings(RATE_LIMITS={**DEFAULT_RATE_LIMITS, 'ip': ('1/minute',)})
    def test_clients_behind_the_trusted_proxy_have_separate_budgets(self):
        for address in ['198.51.100.5', '198.51.100.6']:
            self.assertEqual(self.client.get('/probe/', HTTP_X_FORWARDED_FOR=address).status_code, 200)
        self.assertEqual(self.client.get('/probe/', HTTP_X_FORWARDED_FOR='198.51.100.5').status_code, 429)

    @override_settings(RATE_LIMIT_REDIS_URL='redis://isolated-test:6379/0', RATE_LIMIT_USE_LOCAL=False)
    @patch('core.rate_limits.redis_client', side_effect=ConnectionError('hidden connection details'))
    def test_store_outage_returns_503_and_does_not_execute_the_view(self, redis):
        response = self.client.get('/probe/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response['Retry-After'], '30')
        self.assertEqual(response.json()['code'], 'security_unavailable')
        self.assertNotIn('hidden connection details', response.content.decode())
        view_calls.assert_not_called()

    @override_settings(MAX_REQUEST_BODY_BYTES=100, DATA_UPLOAD_MAX_MEMORY_SIZE=100)
    def test_oversized_json_is_rejected_before_parsing_or_view_work(self):
        response = self.client.post('/probe/', data='x' * 101, content_type='application/json')
        self.assertEqual(response.status_code, 413)
        view_calls.assert_not_called()

    @override_settings(MAX_UPLOAD_REQUEST_BYTES=1024, MAX_UPLOAD_FILE_BYTES=32, FILE_UPLOAD_MAX_MEMORY_SIZE=0)
    def test_oversized_file_is_rejected_and_the_active_temporary_file_is_closed(self):
        from django.core.files.uploadedfile import TemporaryUploadedFile
        created = []

        def track(*args, **kwargs):
            file = TemporaryUploadedFile(*args, **kwargs)
            created.append(file)
            return file

        with patch('django.core.files.uploadhandler.TemporaryUploadedFile', side_effect=track):
            response = self.client.post('/probe/', {'image': SimpleUploadedFile('photo.bin', b'x' * 64)})
        self.assertEqual(response.status_code, 413)
        self.assertTrue(created)
        self.assertTrue(all(file.closed for file in created))
        view_calls.assert_not_called()

    @override_settings(MAX_UPLOAD_REQUEST_BYTES=128)
    def test_total_upload_size_is_bounded(self):
        response = self.client.post('/probe/', {'image': SimpleUploadedFile('photo.bin', b'x' * 256)})
        self.assertEqual(response.status_code, 413)
        view_calls.assert_not_called()

    def test_small_uploads_still_reach_the_view(self):
        response = self.client.post('/probe/', {'image': SimpleUploadedFile('photo.bin', b'x' * 16)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['file_sizes'], [16])

    @override_settings(DATA_UPLOAD_MAX_NUMBER_FILES=1)
    def test_excess_upload_count_is_rejected(self):
        response = self.client.post('/probe/', {
            'first': SimpleUploadedFile('first.bin', b'x'),
            'second': SimpleUploadedFile('second.bin', b'x'),
        })
        self.assertEqual(response.status_code, 400)
        view_calls.assert_not_called()

    def test_all_admin_login_aliases_share_an_ip_budget(self):
        paths = ['/admin/login/', '/dispatch/login/', '/shop/admin/login/']
        for index in range(5):
            response = self.client.post(paths[index % 3], {'phone_number': f'077100000{index}', 'password': 'bad'})
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post(paths[0], {'phone_number': '0771000005'}).status_code, 429)
        self.assertEqual(view_calls.call_count, 5)

    def test_admin_account_budget_survives_changing_ips_and_phone_formats(self):
        for index in range(10):
            response = self.client.post('/dispatch/login/', {'phone_number': '0771000001', 'password': 'bad'},
                                        REMOTE_ADDR=f'198.51.100.{index + 1}')
            self.assertEqual(response.status_code, 200)
        response = self.client.post('/admin/login/', {'username': '+94771000001', 'password': 'bad'},
                                    REMOTE_ADDR='198.51.100.50')
        self.assertEqual(response.status_code, 429)
        self.assertEqual(view_calls.call_count, 10)

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_untrusted_forwarded_proto_cannot_claim_https(self):
        response = self.client.get('/probe/', REMOTE_ADDR='198.51.100.5', HTTP_X_FORWARDED_PROTO='https')
        self.assertEqual(response.status_code, 301)
        self.assertTrue(response['Location'].startswith('https://'))
        view_calls.assert_not_called()

    @override_settings(SECURE_HSTS_SECONDS=3600)
    def test_trusted_https_responses_have_browser_security_headers(self):
        response = self.client.get('/probe/', HTTP_X_FORWARDED_PROTO='https')
        self.assertEqual(response.status_code, 200)
        self.assertIn('max-age=3600', response['Strict-Transport-Security'])
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response['Referrer-Policy'], 'strict-origin-when-cross-origin')

    def test_invalid_host_is_rejected_before_view_work(self):
        response = self.client.get('/probe/', HTTP_HOST='attacker.example')
        self.assertEqual(response.status_code, 400)
        view_calls.assert_not_called()

    def test_anonymous_public_api_still_works(self):
        self.assertEqual(self.client.get('/api/').status_code, 200)


@override_settings(RATE_LIMIT_ENABLED=True)
class AtomicBudgetTests(SimpleTestCase):
    def setUp(self):
        local_store.clear()

    @override_settings(RATE_LIMITS={'ip': ('3/minute',)})
    def test_concurrent_requests_only_admit_the_allowed_number(self):
        def attempt(index):
            try:
                consume_limits([('ip', 'concurrent-test')])
                return True
            except RateLimitExceeded:
                return False

        with ThreadPoolExecutor(max_workers=12) as pool:
            self.assertEqual(sum(pool.map(attempt, range(40))), 3)

    @override_settings(RATE_LIMITS={'ip': ('2/minute', '3/hour')})
    def test_hour_budget_persists_after_the_minute_resets(self):
        with patch('core.rate_limits.time.time', return_value=120010):
            consume_limits([('ip', 'test')])
            consume_limits([('ip', 'test')])
            with self.assertRaises(RateLimitExceeded):
                consume_limits([('ip', 'test')])
        with patch('core.rate_limits.time.time', return_value=120070):
            consume_limits([('ip', 'test')])
            with self.assertRaises(RateLimitExceeded):
                consume_limits([('ip', 'test')])

    @override_settings(RATE_LIMITS={'ip': ('1/minute',), 'checkout': ('1/minute',)})
    def test_rejected_requests_do_not_consume_other_budgets(self):
        consume_limits([('ip', 'first')])
        with self.assertRaises(RateLimitExceeded):
            consume_limits([('ip', 'first'), ('checkout', 'customer')])
        consume_limits([('ip', 'second'), ('checkout', 'customer')])

    @override_settings(RATE_LIMITS={'ip': ('3/minute', '2/minute')})
    def test_same_duration_overrides_do_not_double_count_requests(self):
        consume_limits([('ip', 'test')])
        consume_limits([('ip', 'test')])
        with self.assertRaises(RateLimitExceeded):
            consume_limits([('ip', 'test')])


@skipUnless(os.environ.get('MEALDROP_RATE_LIMIT_TEST_REDIS_URL'), 'Supply a dedicated Redis URL for integration tests.')
@override_settings(RATE_LIMIT_ENABLED=True, RATE_LIMIT_USE_LOCAL=False)
class RedisBudgetTests(SimpleTestCase):
    def setUp(self):
        self.identity = uuid.uuid4().hex
        self.redis_override = override_settings(RATE_LIMIT_REDIS_URL=os.environ['MEALDROP_RATE_LIMIT_TEST_REDIS_URL'])
        self.redis_override.enable()
        self.addCleanup(self.redis_override.disable)

    @override_settings(RATE_LIMITS={'ip': ('3/minute',)})
    def test_concurrent_independent_redis_clients_obey_one_shared_budget(self):
        from redis import Redis
        url = os.environ['MEALDROP_RATE_LIMIT_TEST_REDIS_URL']
        clients = []

        def new_client(url):
            client = Redis.from_url(url, socket_timeout=2)
            clients.append(client)
            return client

        def attempt(index):
            try:
                consume_limits([('ip', self.identity)])
                return True
            except RateLimitExceeded:
                return False

        # All connections address the same Redis process, as separate workers
        # would. Freeze time to keep the test independent of a minute boundary.
        try:
            with patch('core.rate_limits.redis_client', side_effect=new_client), \
                    patch('core.rate_limits.time.time', return_value=120010), ThreadPoolExecutor(max_workers=12) as pool:
                self.assertEqual(sum(pool.map(attempt, range(40))), 3)
        finally:
            for client in clients:
                client.close()

    @override_settings(RATE_LIMITS={'ip': ('2/minute', '3/hour')})
    def test_redis_preserves_the_hour_budget_when_the_minute_changes(self):
        with patch('core.rate_limits.time.time', return_value=120010):
            consume_limits([('ip', self.identity)])
            consume_limits([('ip', self.identity)])
            with self.assertRaises(RateLimitExceeded):
                consume_limits([('ip', self.identity)])
        with patch('core.rate_limits.time.time', return_value=120070):
            consume_limits([('ip', self.identity)])
            with self.assertRaises(RateLimitExceeded):
                consume_limits([('ip', self.identity)])
