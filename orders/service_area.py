"""The primary map polygon is authoritative for customer delivery coverage."""

from math import isfinite

from django.contrib.gis.geos import Point

from .models import DeliveryZone


DELIVERY_DISTRICT = 'Matara'
OUTSIDE_DELIVERY_MESSAGE = (
    'We deliver only within the marked area in Matara District. '
    'This location is outside it. Choose an entrance inside the marked area to get delivery.'
)
AREA_UNAVAILABLE_MESSAGE = 'The Matara delivery area could not be checked. Please try again before saving an address or ordering.'
_UNSET = object()


class DeliveryAreaError(ValueError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def primary_delivery_zone():
    return DeliveryZone.objects.filter(is_active=True, is_primary=True, district=DELIVERY_DISTRICT).order_by('pk').first()


def check_delivery_area(lat, lng, *, zone=_UNSET):
    try:
        latitude, longitude = float(lat), float(lng)
        if not isfinite(latitude) or not isfinite(longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError
    except (TypeError, ValueError, OverflowError) as exc:
        raise DeliveryAreaError('Choose a valid delivery location.', 'invalid_delivery_location') from exc
    if zone is _UNSET:
        zone = primary_delivery_zone()
    if zone is None or zone.polygon.empty or not zone.polygon.valid:
        raise DeliveryAreaError(AREA_UNAVAILABLE_MESSAGE, 'delivery_area_unavailable')
    # Include the boundary itself; holes in the actual polygon remain excluded.
    if not zone.polygon.covers(Point(longitude, latitude, srid=4326)):
        raise DeliveryAreaError(OUTSIDE_DELIVERY_MESSAGE, 'out_of_delivery_area')
    return zone
