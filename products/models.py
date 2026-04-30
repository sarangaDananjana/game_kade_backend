from django.db import models


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
        return f"{self.name}"


class Featured(models.Model):
    topic = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    image = models.ImageField(upload_to='featured_images/')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.topic}"
