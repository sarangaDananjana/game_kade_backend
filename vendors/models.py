from django.conf import settings
from django.contrib.gis.db import models

from core.locations import SpatialLocationMixin


DISTRICT_CHOICES = [('Matara', 'Matara')]


class Vendor(SpatialLocationMixin, models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='shops')
    shop_name = models.CharField(max_length=255)
    logo = models.ImageField(upload_to='vendor_logos/', blank=True, null=True)
    cover_image = models.ImageField(upload_to='vendor_covers/', blank=True, null=True)
    district = models.CharField(max_length=50, choices=DISTRICT_CHOICES, blank=True, null=True)
    lat = models.FloatField(help_text='Latitude coordinate')
    lng = models.FloatField(help_text='Longitude coordinate')
    location_point = models.PointField(null=True, blank=True, geography=True)
    h3_index = models.CharField(max_length=15, blank=True, null=True, help_text='H3 spatial index (Resolution 8)')
    is_pickup_only = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.shop_name
