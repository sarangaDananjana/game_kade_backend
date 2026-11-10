"""DRF limits for verified users and costly endpoints, plus phone OTP budgets."""

from django.core.exceptions import RequestDataTooBig
from rest_framework.exceptions import APIException, Throttled
from rest_framework.throttling import BaseThrottle
from rest_framework.views import exception_handler

from .rate_limits import RateLimitExceeded, RateLimitUnavailable, consume_limits
from .security import client_address
from django.conf import settings


class SecurityUnavailable(APIException):
    status_code = 503
    default_detail = 'Request protection is temporarily unavailable.'
    default_code = 'security_unavailable'


def enforce_limits(scopes):
    try:
        consume_limits(scopes)
    except RateLimitExceeded as error:
        raise Throttled(wait=error.wait) from None
    except RateLimitUnavailable:
        raise SecurityUnavailable() from None


class APIRateLimitThrottle(BaseThrottle):
    def allow_request(self, request, view):
        ip = getattr(request._request, 'client_ip', None)
        if ip is None:
            ip, _ = client_address(request.META, settings.TRUSTED_PROXY_CIDRS)
        identity = str(request.user.pk) if request.user.is_authenticated else ip
        scopes = []
        if request.user.is_authenticated:
            scopes.append(('api_user', identity))
            if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                scopes.append(('api_write_user', identity))
        scope = getattr(view, 'rate_limit_scope', None)
        if scope:
            scopes.append((scope, ip if scope.endswith('_ip') else identity))
        global_scope = getattr(view, 'rate_limit_global_scope', None)
        if global_scope:
            scopes.append((global_scope, 'all'))
        enforce_limits(scopes)
        return True


def api_exception_handler(exc, context):
    if isinstance(exc, RequestDataTooBig):
        from rest_framework.response import Response
        return Response({'detail': 'Request body is too large.', 'code': 'request_too_large'}, status=413)
    response = exception_handler(exc, context)
    if response is not None and isinstance(exc, (Throttled, SecurityUnavailable)):
        response.data['code'] = 'rate_limited' if isinstance(exc, Throttled) else 'security_unavailable'
        response['Cache-Control'] = 'no-store'
        if isinstance(exc, SecurityUnavailable):
            response['Retry-After'] = '30'
    return response
