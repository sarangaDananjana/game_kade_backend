import uuid
from django.contrib.gis.db import models
from django.conf import settings
from django.contrib.gis.geos import Point
from products.models import Product

User = settings.AUTH_USER_MODEL


class DeliveryZone(models.Model):
    name = models.CharField(max_length=100)
    polygon = models.PolygonField(
        help_text="Draw the delivery zone on the map")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name}"


class OrderLocation(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name='locations', null=True, blank=True)
    name = models.CharField(
        max_length=255, help_text="e.g., University Canteen, My Boarding")
    lat = models.FloatField(help_text="Latitude coordinate")
    lng = models.FloatField(help_text="Longitude coordinate")

    # 2. NEW FIELD: Spatial Point for PostGIS math
    location_point = models.PointField(null=True, blank=True, geography=True)

    description = models.TextField(
        help_text="Landmark or additional details", blank=True, null=True)
    unique_identity = models.CharField(
        max_length=100, help_text="Two word sentence to identify location")
    is_system_defined = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        # 3. MAGIC HAPPENS HERE: Auto-convert Lat/Lng to a PostGIS Point
        if self.lat and self.lng:
            # Note: Point takes (longitude, latitude) - this is standard GIS format
            self.location_point = Point(self.lng, self.lat)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.unique_identity}) - {'System' if self.is_system_defined else 'User'}"


class Order(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('preparing', 'Preparing'),
        ('out_for_delivery', 'Out for Delivery'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='orders')
    location = models.ForeignKey(
        OrderLocation, on_delete=models.SET_NULL, null=True)

    # 4. NEW FIELDS: Zone and Rider
    zone = models.ForeignKey(DeliveryZone, on_delete=models.SET_NULL,
                             null=True, blank=True, related_name='orders')
    rider = models.ForeignKey(User, on_delete=models.SET_NULL,
                              null=True, blank=True, related_name='assigned_deliveries')

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='pending')
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    delivery_code = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    quantity = models.PositiveIntegerField()
    # We freeze the price here so if the product price changes later, historical orders remain accurate
    price_at_purchase = models.DecimalField(max_digits=10, decimal_places=2)
