from django.db import models
from django.conf import settings
from products.models import Product

User = settings.AUTH_USER_MODEL


class OrderLocation(models.Model):
    # If user is null, it means it's a "System Defined" location (like a University Canteen)
    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name='custom_locations', null=True, blank=True)
    name = models.CharField(
        max_length=255, help_text="e.g., University Canteen, My Boarding")
    address = models.TextField()
    is_system_defined = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.name} - {'System' if self.is_system_defined else 'User'}"


class Cart(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='cart')
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def total_cart_price(self):
        return sum(item.total_price for item in self.items.all())


class CartItem(models.Model):
    cart = models.ForeignKey(
        Cart, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)

    @property
    def total_price(self):
        return self.product.price * self.quantity


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
    created_at = models.DateTimeField(auto_now_add=True)


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    quantity = models.PositiveIntegerField()
    # We freeze the price here so if the product price changes later, historical orders remain accurate
    price_at_purchase = models.DecimalField(max_digits=10, decimal_places=2)
