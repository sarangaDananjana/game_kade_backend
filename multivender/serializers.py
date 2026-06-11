from rest_framework import serializers
from .models import Vendor, Product, OrderLocation, Order, OrderItem
from django.contrib.auth import get_user_model

User = get_user_model()

class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ['id', 'name', 'category', 'description', 'price', 'image', 'stock_quantity', 'is_available']

class VendorSerializer(serializers.ModelSerializer):
    products = ProductSerializer(many=True, read_only=True)

    class Meta:
        model = Vendor
        fields = ['id', 'shop_name', 'logo', 'cover_image', 'district', 'lat', 'lng', 'is_pickup_only', 'is_active', 'products']

class OrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'product', 'product_name', 'quantity', 'price_at_purchase']
        read_only_fields = ['price_at_purchase']

class OrderLocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderLocation
        fields = ['id', 'name', 'district', 'lat', 'lng', 'description', 'unique_identity', 'is_system_defined']
        read_only_fields = ['user', 'is_system_defined']

class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True)
    location = serializers.PrimaryKeyRelatedField(queryset=OrderLocation.objects.all(), required=False, allow_null=True)
    shop_name = serializers.CharField(source='vendor.shop_name', read_only=True)

    class Meta:
        model = Order
        fields = ['id', 'vendor', 'shop_name', 'location', 'status', 'total_amount', 'delivery_code', 'created_at', 'items']
        read_only_fields = ['status', 'delivery_code', 'created_at', 'total_amount']

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        user = validated_data.pop('user', self.context['request'].user)

        # Calculate total amount
        total_amount = 0
        for item_data in items_data:
            product = item_data['product']
            total_amount += product.price * item_data['quantity']

        # Create Order
        order = Order.objects.create(user=user, total_amount=total_amount, **validated_data)

        # Create Order Items
        for item_data in items_data:
            product = item_data['product']
            OrderItem.objects.create(
                order=order,
                product=product,
                quantity=item_data['quantity'],
                price_at_purchase=product.price
            )

        return order
