"""Atomic, shared request budgets. Local storage is only for development/tests."""

import logging
import math
import threading
import time
from functools import lru_cache

from django.conf import settings
from django.utils.crypto import salted_hmac

from .security import parse_rate

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    def __init__(self, wait):
        self.wait = wait
        super().__init__('Too many requests.')


class RateLimitUnavailable(Exception):
    pass


# Check every applicable budget before incrementing any of them. Redis executes
# the script atomically, so concurrent Gunicorn workers cannot exceed a budget.
CONSUME_SCRIPT = """
local wait = 0
for i, key in ipairs(KEYS) do
    local count = tonumber(redis.call('GET', key) or '0')
    if count >= tonumber(ARGV[(i-1)*2+1]) then
        wait = math.max(wait, tonumber(ARGV[(i-1)*2+2]))
    end
end
if wait > 0 then return wait end
for i, key in ipairs(KEYS) do
    local count = redis.call('INCR', key)
    if count == 1 then
        redis.call('EXPIRE', key, tonumber(ARGV[(i-1)*2+2]) + 1)
    end
end
return 0
"""


@lru_cache(maxsize=4)
def redis_client(url):
    from redis import Redis

    return Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1, health_check_interval=30)


class LocalRateLimitStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.counters = {}
        self.last_cleanup = 0

    def clear(self):
        with self.lock:
            self.counters.clear()
            self.last_cleanup = 0

    def consume(self, budgets, now):
        with self.lock:
            if now - self.last_cleanup >= 60:
                self.counters = {key: item for key, item in self.counters.items() if item[1] > now}
                self.last_cleanup = now
            wait = max((seconds for key, limit, seconds in budgets
                        if self.counters.get(key, (0, 0))[0] >= limit), default=0)
            if wait:
                return wait
            for key, limit, seconds in budgets:
                count, expires = self.counters.get(key, (0, now + seconds + 1))
                self.counters[key] = (count + 1, expires)
            return 0


local_store = LocalRateLimitStore()
_error_lock = threading.Lock()
_last_error_log = 0


def consume_limits(scopes):
    if not getattr(settings, 'RATE_LIMIT_ENABLED', True):
        return
    now = time.time()
    budget_map = {}
    for scope, identity in scopes:
        digest = salted_hmac('meal-drop-rate-limit', f'{scope}:{identity}', algorithm='sha256').hexdigest()
        for rate in settings.RATE_LIMITS[scope]:
            limit, duration = parse_rate(rate)
            bucket = int(now // duration)
            seconds = max(1, math.ceil((bucket + 1) * duration - now))
            key = f'mealdrop:rate:v1:{digest}:{duration}:{bucket}'
            previous = budget_map.get(key, (limit, seconds))
            budget_map[key] = (min(limit, previous[0]), seconds)
    budgets = [(key, limit, seconds) for key, (limit, seconds) in budget_map.items()]
    if not budgets:
        return
    try:
        if settings.RATE_LIMIT_REDIS_URL:
            arguments = [value for key, limit, seconds in budgets for value in (limit, seconds)]
            wait = redis_client(settings.RATE_LIMIT_REDIS_URL).eval(
                CONSUME_SCRIPT, len(budgets), *(key for key, limit, seconds in budgets), *arguments,
            )
        elif getattr(settings, 'RATE_LIMIT_USE_LOCAL', False):
            wait = local_store.consume(budgets, now)
        else:
            raise RateLimitUnavailable()
    except Exception as error:
        # Fail closed and bound logging during an outage. Never log a Redis URL,
        # phone number, authorization header, or request body.
        global _last_error_log
        with _error_lock:
            if now - _last_error_log >= 30:
                logger.error('Rate limit store unavailable (%s).', type(error).__name__)
                _last_error_log = now
        raise RateLimitUnavailable('Request protection is temporarily unavailable.') from None
    if wait:
        raise RateLimitExceeded(int(wait))
