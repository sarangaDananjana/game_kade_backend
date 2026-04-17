import uuid
from django.db import models
from django.conf import settings
from products.models import Product

User = settings.AUTH_USER_MODEL


class OrderLocation(models.Model):
    # If user is null, it means it's a "System Defined" location (like a University Canteen)
    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name='locations', null=True, blank=True)
    name = models.CharField(
        max_length=255, help_text="e.g., University Canteen, My Boarding")
    lat = models.FloatField(help_text="Latitude coordinate")
    lng = models.FloatField(help_text="Longitude coordinate")
    description = models.TextField(
        help_text="Landmark or additional details to find the place", blank=True, null=True)
    unique_identity = models.CharField(
        max_length=100, help_text="Two word sentence to identify location, e.g., 'Blue Gate'")
    is_system_defined = models.BooleanField(default=False)

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
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='pending')
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    delivery_code = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True, help_text="Unique code for rider QR scan")
    created_at = models.DateTimeField(auto_now_add=True)


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    quantity = models.PositiveIntegerField()
    # We freeze the price here so if the product price changes later, historical orders remain accurate
    price_at_purchase = models.DecimalField(max_digits=10, decimal_places=2)
