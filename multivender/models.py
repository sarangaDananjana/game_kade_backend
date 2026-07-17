import uuid
import h3
from django.conf import settings  # Import settings to access the User model safely
from django.contrib.gis.db import models
from django.contrib.gis.geos import Point

DISTRICT_CHOICES = (
    ('Ampara', 'Ampara'),
    ('Anuradhapura', 'Anuradhapura'),
    ('Badulla', 'Badulla'),
    ('Batticaloa', 'Batticaloa'),
    ('Colombo', 'Colombo'),
    ('Galle', 'Galle'),
    ('Gampaha', 'Gampaha'),
    ('Hambantota', 'Hambantota'),
    ('Jaffna', 'Jaffna'),
    ('Kalutara', 'Kalutara'),
    ('Kandy', 'Kandy'),
    ('Kegalle', 'Kegalle'),
    ('Kilinochchi', 'Kilinochchi'),
    ('Kurunegala', 'Kurunegala'),
    ('Mannar', 'Mannar'),
    ('Matale', 'Matale'),
    ('Matara', 'Matara'),
    ('Monaragala', 'Monaragala'),
    ('Mullaitivu', 'Mullaitivu'),
    ('Nuwara Eliya', 'Nuwara Eliya'),
    ('Polonnaruwa', 'Polonnaruwa'),
    ('Puttalam', 'Puttalam'),
    ('Ratnapura', 'Ratnapura'),
    ('Trincomalee', 'Trincomalee'),
    ('Vavuniya', 'Vavuniya'),
)

# ------------------------------------------------------------------------
# Delivery Zone
# ------------------------------------------------------------------------


class DeliveryZone(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name

# ------------------------------------------------------------------------
# Delivery Pricing Model
# ------------------------------------------------------------------------

class DeliveryDistanceTier(models.Model):
    grid_distance = models.PositiveIntegerField(
        unique=True, 
        help_text="H3 grid distance (0 for same tile, 1 for adjacent, etc.)"
    )
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2)
    
    def __str__(self):
        return f"Distance {self.grid_distance} (R{self.grid_distance + 1}) - Rs {self.delivery_fee}"

# ------------------------------------------------------------------------
# Vendor Model
# ------------------------------------------------------------------------


class Vendor(models.Model):
    # Using settings.AUTH_USER_MODEL to link safely to your custom 'user' app
    owner = models.ForeignKey(settings.AUTH_USER_MODEL,
                              on_delete=models.CASCADE, related_name='shops')
    shop_name = models.CharField(max_length=255)

    logo = models.ImageField(upload_to='vendor_logos/', blank=True, null=True)
    cover_image = models.ImageField(upload_to='vendor_covers/', blank=True, null=True)

    district = models.CharField(max_length=50, choices=DISTRICT_CHOICES, blank=True, null=True)

    lat = models.FloatField(help_text="Latitude coordinate")
    lng = models.FloatField(help_text="Longitude coordinate")
    location_point = models.PointField(null=True, blank=True, geography=True)
    h3_index = models.CharField(max_length=15, blank=True, null=True, help_text="H3 spatial index (Resolution 8)")

    is_pickup_only = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.lat and self.lng:
            self.location_point = Point(self.lng, self.lat)
            try:
                self.h3_index = h3.latlng_to_cell(self.lat, self.lng, 8)
            except AttributeError:
                self.h3_index = h3.geo_to_h3(self.lat, self.lng, 8)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.shop_name

# ------------------------------------------------------------------------
# Product Model
# ------------------------------------------------------------------------


class Product(models.Model):
    CATEGORY_CHOICES = (
        ('kottu', 'Kottu'),
        ('fried_rice', 'Fried Rice'),
        ('rice_curry', 'Rice & Curry'),
        ('short_eats', 'Short Eats'),
        ('beverages', 'Beverages'),
        ('dessert', 'Dessert'),
        ('other', 'Other'),
    )

    vendor = models.ForeignKey(
        Vendor, on_delete=models.CASCADE, related_name='products')
    name = models.CharField(max_length=255)
    category = models.CharField(
        max_length=50, choices=CATEGORY_CHOICES, default='other')
    description = models.TextField(blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    image = models.ImageField(
        upload_to='product_images/', blank=True, null=True)
    stock_quantity = models.PositiveIntegerField(default=0)
    is_available = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} - {self.vendor.shop_name}"

# ------------------------------------------------------------------------
# Order Location
# ------------------------------------------------------------------------


class OrderLocation(models.Model):
    # Using settings.AUTH_USER_MODEL here as well
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='multivendor_locations', null=True, blank=True)
    name = models.CharField(
        max_length=255, help_text="e.g., University Canteen, My Boarding")
    district = models.CharField(max_length=50, choices=DISTRICT_CHOICES, blank=True, null=True)
    lat = models.FloatField(help_text="Latitude coordinate")
    lng = models.FloatField(help_text="Longitude coordinate")
    location_point = models.PointField(null=True, blank=True, geography=True)
    h3_index = models.CharField(max_length=15, blank=True, null=True, help_text="H3 spatial index (Resolution 8)")
    description = models.TextField(
        help_text="Landmark or additional details", blank=True, null=True)
    unique_identity = models.CharField(
        max_length=100, help_text="Two word sentence to identify location")
    is_system_defined = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if self.lat and self.lng:
            self.location_point = Point(self.lng, self.lat)
            try:
                self.h3_index = h3.latlng_to_cell(self.lat, self.lng, 8)
            except AttributeError:
                self.h3_index = h3.geo_to_h3(self.lat, self.lng, 8)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.unique_identity}) - {'System' if self.is_system_defined else 'User'}"

# ------------------------------------------------------------------------
# Order Model
# ------------------------------------------------------------------------


class Order(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('preparing', 'Preparing'),
        ('out_for_delivery', 'Out for Delivery'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
    )

    # Safely linking both the buyer and the rider to your User app
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE, related_name='multivendor_orders')
    vendor = models.ForeignKey(
        Vendor, on_delete=models.CASCADE, related_name='orders')
    location = models.ForeignKey(
        OrderLocation, on_delete=models.SET_NULL, null=True)
    zone = models.ForeignKey('orders.DeliveryZone', on_delete=models.SET_NULL,
                             null=True, blank=True, related_name='multivendor_orders')
    rider = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                              null=True, blank=True, related_name='multivendor_assigned_deliveries')

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='pending')
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    delivery_code = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Order {self.id} - {self.vendor.shop_name} - {self.status}"

# ------------------------------------------------------------------------
# Order Item Model
# ------------------------------------------------------------------------


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    quantity = models.PositiveIntegerField()
    price_at_purchase = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.quantity} x {self.product.name if self.product else 'Deleted Product'}"
