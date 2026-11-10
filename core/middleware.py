"""Reject abuse before authentication, database queries, and external calls."""

from django.conf import settings
from django.core.exceptions import RequestDataTooBig, TooManyFieldsSent, TooManyFilesSent
from django.core.files.uploadhandler import FileUploadHandler
from django.http import JsonResponse

from .rate_limits import RateLimitExceeded, RateLimitUnavailable, consume_limits
from .security import client_address


def guard_response(detail, code, status, *, wait=None):
    response = JsonResponse({'detail': detail, 'code': code}, status=status)
    response['Cache-Control'] = 'no-store'
    if wait is not None:
        response['Retry-After'] = str(wait)
    return response


class TrustedProxyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.client_ip, trusted_peer = client_address(request.META, settings.TRUSTED_PROXY_CIDRS)
        for header in ('HTTP_FORWARDED', 'HTTP_X_REAL_IP', 'HTTP_X_FORWARDED_HOST', 'HTTP_X_FORWARDED_PORT'):
            request.META.pop(header, None)
        proto = request.META.get('HTTP_X_FORWARDED_PROTO', '').strip().lower()
        if not trusted_peer or proto not in {'http', 'https'}:
            request.META.pop('HTTP_X_FORWARDED_PROTO', None)
        else:
            request.META['HTTP_X_FORWARDED_PROTO'] = proto
        if trusted_peer:
            request.META['HTTP_X_FORWARDED_FOR'] = request.client_ip
        else:
            request.META.pop('HTTP_X_FORWARDED_FOR', None)
        return self.get_response(request)


class BoundedUploadHandler(FileUploadHandler):
    def __init__(self, request):
        super().__init__(request)
        self.total_bytes = 0
        self.file_bytes = 0

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        self.file_bytes = 0

    def receive_data_chunk(self, raw_data, start):
        self.total_bytes += len(raw_data)
        self.file_bytes += len(raw_data)
        if (self.file_bytes > settings.MAX_UPLOAD_FILE_BYTES or
                self.total_bytes > settings.MAX_UPLOAD_REQUEST_BYTES):
            # The parser closes completed files on errors; also close the
            # active file, which has not been added to request.FILES yet.
            for handler in self.request.upload_handlers:
                file = getattr(handler, 'file', None)
                if file is not None:
                    file.close()
            raise RequestDataTooBig('Upload exceeds the configured size limit.')
        return raw_data

    def file_complete(self, file_size):
        return None


class RequestGuardMiddleware:
    LOGIN_PATHS = {'/admin/login', '/dispatch/login', '/shop/admin/login'}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Validate Host even for requests rejected before Django resolves a URL.
        request.get_host()
        try:
            scopes = [('ip', request.client_ip)]
            login_attempt = request.method == 'POST' and request.path_info.rstrip('/') in self.LOGIN_PATHS
            if login_attempt:
                scopes.append(('admin_login_ip', request.client_ip))
            consume_limits(scopes)
            multipart = request.content_type == 'multipart/form-data'
            limit = settings.MAX_UPLOAD_REQUEST_BYTES if multipart else settings.MAX_REQUEST_BODY_BYTES
            length = request.META.get('CONTENT_LENGTH')
            if length:
                try:
                    length = int(length)
                except (TypeError, ValueError):
                    return guard_response('Invalid Content-Length.', 'invalid_request', 400)
                if length < 0:
                    return guard_response('Invalid Content-Length.', 'invalid_request', 400)
                if length > limit:
                    return guard_response('Request body is too large.', 'request_too_large', 413)
            if multipart:
                request.upload_handlers.insert(0, BoundedUploadHandler(request))
                # Parse within this middleware so size/count violations produce
                # a consistent 413/400 before reaching a view.
                request._load_post_and_files()
            elif len(request.body) > limit:
                return guard_response('Request body is too large.', 'request_too_large', 413)
            if login_attempt:
                account = request.POST.get('phone_number') or request.POST.get('username', '')
                if account:
                    from users.utils import normalize_mobile_number
                    try:
                        account = normalize_mobile_number(account)
                    except ValueError:
                        account = account.strip().lower()[:255]
                    consume_limits([('admin_login_account', account)])
        except RateLimitExceeded as error:
            return guard_response('Too many requests. Try again later.', 'rate_limited', 429, wait=error.wait)
        except RateLimitUnavailable:
            return guard_response('Request protection is temporarily unavailable.', 'security_unavailable', 503, wait=30)
        except RequestDataTooBig:
            return guard_response('Request body or upload is too large.', 'request_too_large', 413)
        except (TooManyFieldsSent, TooManyFilesSent):
            return guard_response('Too many form fields or files.', 'invalid_request', 400)
        return self.get_response(request)
