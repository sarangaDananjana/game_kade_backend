"""Road geometry is fetched on demand, never by Jobs/dashboard polling."""
import hashlib
import json
import math
from datetime import timedelta
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from core.timeutils import sri_lanka_isoformat

from .models import RiderRoadRoute
from .routes import session_stops

CACHE_HOURS = 24


def _google_directions(points, api_key):
    params = {'origin': points[0], 'destination': points[-1], 'mode': 'driving',
        'alternatives': 'false', 'units': 'metric', 'region': 'lk', 'key': api_key}
    if len(points) > 2:
        params['waypoints'] = '|'.join(points[1:-1])  # Keep the displayed stop order.
    with urlopen('https://maps.googleapis.com/maps/api/directions/json?' + urlencode(params), timeout=12) as response:
        body = json.load(response)
    if body.get('status') != 'OK' or not body.get('routes'):
        # Never return Google's request URL, API key or internal error message.
        return {'status': 'unavailable', 'polylines': [], 'message': 'Road route unavailable. You can still open Google Maps directions.'}
    route = body['routes'][0]
    polyline = route.get('overview_polyline', {}).get('points')
    if not polyline:
        return {'status': 'unavailable', 'polylines': [], 'message': 'Road route unavailable. You can still open Google Maps directions.'}
    return {'status': 'ready', 'polylines': [polyline],
        'distance_meters': sum(leg['distance']['value'] for leg in route['legs']),
        'duration_seconds': sum(leg['duration']['value'] for leg in route['legs']),
        'copyrights': route.get('copyrights', '')}


def _cached_segment(points, budget=None):
    api_key = getattr(settings, 'GOOGLE_DIRECTIONS_API_KEY', '') or getattr(settings, 'GOOGLE_MAPS_API_KEY', '')
    # Key rotation clears negative cache entries without exposing the key.
    key = hashlib.sha256(json.dumps(['driving-v1', points, api_key]).encode()).hexdigest()
    now = timezone.now()
    with transaction.atomic():
        RiderRoadRoute.objects.get_or_create(key=key)
        cached = RiderRoadRoute.objects.select_for_update().get(pk=key)
        if cached.expires_at and cached.expires_at > now:
            return {**cached.payload, 'expires_at': sri_lanka_isoformat(cached.expires_at)}
        if cached.lease_until and cached.lease_until > now:
            return {'status': 'pending', 'polylines': [], 'retry_after_seconds': 3}
        if budget is not None:
            if budget[0] >= 1:
                return {'status': 'pending', 'polylines': [], 'retry_after_seconds': 1}
            budget[0] += 1
        lease = now + timedelta(seconds=30)
        cached.lease_until = lease
        cached.save(update_fields=['lease_until', 'updated_at'])
    # Release database locks during the paid request; other workers see the lease.
    try:
        payload = _google_directions(points, api_key) if api_key else {
            'status': 'unavailable', 'polylines': [], 'message': 'Road route unavailable. You can still open Google Maps directions.'}
    except (URLError, TimeoutError, ValueError, KeyError, TypeError, OSError):
        payload = {'status': 'unavailable', 'polylines': [], 'message': 'Road route unavailable. You can still open Google Maps directions.'}
    ttl = timedelta(hours=CACHE_HOURS) if payload['status'] == 'ready' else timedelta(minutes=5)
    expires_at = timezone.now() + ttl
    # A superseded lease cannot overwrite a newer route.
    RiderRoadRoute.objects.filter(pk=key, lease_until=lease).update(payload=payload, expires_at=expires_at, lease_until=None)
    return {**payload, 'expires_at': sri_lanka_isoformat(expires_at)}


def road_route(session):
    stops = session_stops(session)
    if not session.vendor or not stops:
        return {'status': 'unavailable', 'polylines': [], 'message': 'Pickup and delivery coordinates are needed for a road route.'}
    coords = [(session.vendor.lat, session.vendor.lng)] + [(s['lat'], s['lng']) for s in stops]
    if any(not math.isfinite(lat) or not math.isfinite(lng) or abs(lat) > 90 or abs(lng) > 180 for lat, lng in coords):
        return {'status': 'unavailable', 'polylines': [], 'message': 'Some delivery coordinates are invalid.'}
    points = [f'{lat:.6f},{lng:.6f}' for lat, lng in coords]
    # Google permits 25 intermediate waypoints; adjoining chunks share an endpoint.
    segments = []
    # Limit a web request to one cache miss / paid call, even on very large runs.
    # Later calls reuse finished chunks before requesting the next missing chunk.
    budget = [0]
    for i in range(0, len(points) - 1, 26):
        segment = _cached_segment(points[i:i + 27], budget)
        if segment['status'] != 'ready':
            return segment
        segments.append(segment)
    return {'status': 'ready', 'polylines': [p for segment in segments for p in segment['polylines']],
        'distance_meters': sum(s['distance_meters'] for s in segments),
        'duration_seconds': sum(s['duration_seconds'] for s in segments),
        'expires_at': min(s['expires_at'] for s in segments),
        'copyrights': ' · '.join(dict.fromkeys(s.get('copyrights', '') for s in segments))}
