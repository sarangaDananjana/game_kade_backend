"""Keep coordinates, PostGIS points, and H3 cells in sync."""

import h3
from django.contrib.gis.geos import Point


class SpatialLocationMixin:
    def save(self, *args, **kwargs):
        if self.lat is not None and self.lng is not None:
            self.location_point = Point(self.lng, self.lat, srid=4326)
            self.h3_index = h3.latlng_to_cell(self.lat, self.lng, 8)
            update_fields = kwargs.get('update_fields')
            if update_fields is not None and {'lat', 'lng'} & set(update_fields):
                kwargs['update_fields'] = set(update_fields) | {'location_point', 'h3_index'}
        return super().save(*args, **kwargs)
