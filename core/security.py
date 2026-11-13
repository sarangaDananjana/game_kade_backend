"""Security configuration shared by the application and isolated tests."""

import ipaddress
import re

from django.core.exceptions import ImproperlyConfigured
from users.phone_numbers import normalize_mobile_number


DEFAULT_OTP_SMS_BYPASS_NUMBERS = '0774610536,0721992125'


DEFAULT_RATE_LIMITS = {
    'ip': ('240/minute', '10000/hour'),
    'api_user': ('120/minute', '5000/hour'),
    'api_write_user': ('30/minute',),
    'otp_send_ip': ('5/minute', '30/hour'),
    'otp_send_phone': ('1/minute', '5/hour'),
    'otp_send_global': ('30/minute', '300/hour'),
    'otp_verify_ip': ('15/minute', '100/hour'),
    'otp_verify_phone': ('10/minute', '30/hour'),
    'token_refresh_ip': ('20/minute',),
    'admin_login_ip': ('5/minute', '30/hour'),
    'admin_login_account': ('10/hour',),
    'checkout': ('5/minute', '50/hour'),
    'delivery_quote': ('20/minute',),
    'route_optimize': ('3/minute', '30/hour'),
}


def env_bool(environment, name, default):
    value = environment.get(name, str(default)).strip().lower()
    if value in {'true', '1', 'yes'}:
        return True
    if value in {'false', '0', 'no'}:
        return False
    raise ImproperlyConfigured(f'{name} must be true or false.')


def env_list(environment, name, default=''):
    return [value.strip() for value in environment.get(name, default).split(',') if value.strip()]


def env_int(environment, name, default):
    try:
        value = int(environment.get(name, default))
    except (TypeError, ValueError):
        raise ImproperlyConfigured(f'{name} must be a positive integer.') from None
    if value <= 0:
        raise ImproperlyConfigured(f'{name} must be a positive integer.')
    return value


def parse_rate(value):
    match = re.fullmatch(r'([1-9][0-9]*)/(second|minute|hour|day)', value)
    if not match:
        raise ImproperlyConfigured('Rate limits must use count/second, count/minute, count/hour, or count/day.')
    return int(match[1]), {'second': 1, 'minute': 60, 'hour': 3600, 'day': 86400}[match[2]]


def signing_key(environment, *, debug):
    value = environment.get('SECRET_KEY', '')
    if debug and not value:
        return 'django-insecure-local-development-only'
    if not debug and (len(value) < 50 or len(set(value)) < 5 or value.startswith('django-insecure-')):
        raise ImproperlyConfigured('Set SECRET_KEY to a private random value of at least 50 characters.')
    return value


def security_settings(environment, *, debug):
    redis_url = environment.get('RATE_LIMIT_REDIS_URL', '').strip()
    if not redis_url and not debug:
        raise ImproperlyConfigured('RATE_LIMIT_REDIS_URL is required in production. Use the private Redis service.')
    if redis_url and not redis_url.startswith(('redis://', 'rediss://')):
        raise ImproperlyConfigured('RATE_LIMIT_REDIS_URL must use redis:// or rediss://.')
    rates = {}
    for scope, defaults in DEFAULT_RATE_LIMITS.items():
        rates[scope] = tuple(env_list(environment, f'RATE_LIMIT_{scope.upper()}', ','.join(defaults)))
        if not rates[scope]:
            raise ImproperlyConfigured(f'RATE_LIMIT_{scope.upper()} cannot be empty.')
        for rate in rates[scope]:
            parse_rate(rate)
    proxies = env_list(environment, 'TRUSTED_PROXY_CIDRS', '127.0.0.1/32,::1/128')
    try:
        for proxy in proxies:
            ipaddress.ip_network(proxy)
    except ValueError:
        raise ImproperlyConfigured('TRUSTED_PROXY_CIDRS must contain valid IP addresses or networks.') from None
    max_attempts = env_int(environment, 'OTP_MAX_FAILED_ATTEMPTS', 5)
    if max_attempts > 10:
        raise ImproperlyConfigured('OTP_MAX_FAILED_ATTEMPTS cannot exceed 10.')
    try:
        bypass_numbers = {
            normalize_mobile_number(number)
            for number in env_list(environment, 'OTP_SMS_BYPASS_NUMBERS', DEFAULT_OTP_SMS_BYPASS_NUMBERS)
        }
    except ValueError:
        raise ImproperlyConfigured('OTP_SMS_BYPASS_NUMBERS must contain valid Sri Lankan mobile numbers.') from None
    body_limit = env_int(environment, 'MAX_REQUEST_BODY_BYTES', 1024 * 1024)
    return {
        'RATE_LIMIT_ENABLED': True,
        'RATE_LIMIT_REDIS_URL': redis_url,
        'RATE_LIMIT_USE_LOCAL': debug and not redis_url,
        'RATE_LIMITS': rates,
        'TRUSTED_PROXY_CIDRS': proxies,
        'SECURE_PROXY_SSL_HEADER': ('HTTP_X_FORWARDED_PROTO', 'https'),
        'MAX_REQUEST_BODY_BYTES': body_limit,
        'MAX_UPLOAD_REQUEST_BYTES': env_int(environment, 'MAX_UPLOAD_REQUEST_BYTES', 10 * 1024 * 1024),
        'MAX_UPLOAD_FILE_BYTES': env_int(environment, 'MAX_UPLOAD_FILE_BYTES', 5 * 1024 * 1024),
        'DATA_UPLOAD_MAX_MEMORY_SIZE': body_limit,
        'DATA_UPLOAD_MAX_NUMBER_FIELDS': 1000,
        'DATA_UPLOAD_MAX_NUMBER_FILES': 5,
        'FILE_UPLOAD_MAX_MEMORY_SIZE': 256 * 1024,
        'OTP_MAX_FAILED_ATTEMPTS': max_attempts,
        'OTP_SMS_BYPASS_NUMBERS': bypass_numbers,
        'SECURE_SSL_REDIRECT': env_bool(environment, 'SECURE_SSL_REDIRECT', not debug),
        'SECURE_HSTS_SECONDS': env_int(environment, 'SECURE_HSTS_SECONDS', 86400) if not debug else 0,
        'SECURE_HSTS_INCLUDE_SUBDOMAINS': False,
        'SECURE_HSTS_PRELOAD': False,
        'SESSION_COOKIE_SECURE': not debug,
        'SESSION_COOKIE_HTTPONLY': True,
        'SESSION_COOKIE_SAMESITE': 'Lax',
        'CSRF_COOKIE_SECURE': not debug,
        'CSRF_COOKIE_SAMESITE': 'Lax',
        'SECURE_CONTENT_TYPE_NOSNIFF': True,
        'SECURE_REFERRER_POLICY': 'strict-origin-when-cross-origin',
        'X_FRAME_OPTIONS': 'DENY',
    }


def client_address(meta, proxy_cidrs):
    """Walk a proxy chain from the socket peer; never trust a client's leftmost IP."""
    try:
        peer = ipaddress.ip_address(meta.get('REMOTE_ADDR', ''))
    except ValueError:
        return 'unknown', False
    networks = [ipaddress.ip_network(value) for value in proxy_cidrs]

    def trusted(address):
        return any(address in network for network in networks)

    if not trusted(peer):
        return str(peer), False
    forwarded = meta.get('HTTP_X_FORWARDED_FOR', '')
    # Invalid or unusually long chains share the proxy's bucket instead of
    # supplying a fresh identity on every request.
    if not forwarded or len(forwarded) > 1024 or forwarded.count(',') > 15:
        return str(peer), True
    try:
        chain = [ipaddress.ip_address(value.strip()) for value in forwarded.split(',')]
    except ValueError:
        return str(peer), True
    address = peer
    for hop in reversed(chain):
        if not trusted(address):
            break
        address = hop
    return str(address), True
