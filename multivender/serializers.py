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
    delivery_fee = serializers.SerializerMethodField()
    is_available = serializers.SerializerMethodField()

    class Meta:
        model = Vendor
        fields = ['id', 'shop_name', 'logo', 'cover_image', 'district', 'lat', 'lng', 'is_pickup_only', 'is_active', 'products', 'delivery_fee', 'is_available', 'h3_index']

    def get_delivery_fee(self, obj):
        order_location = self.context.get('order_location')
        if order_location and obj.h3_index and order_location.h3_index:
            from multivender.services.pricing import calculate_delivery_fee
            try:
                return calculate_delivery_fee(obj, order_location)
            except ValueError:
                return None
        return None

    def get_is_available(self, obj):
        order_location = self.context.get('order_location')
        if order_location and obj.h3_index and order_location.h3_index:
            import h3
            try:
                # User requirement: > R4 (distance > 3) is faded
                dist = h3.grid_distance(obj.h3_index, order_location.h3_index)
                if dist > 3:
                    return False
                return True
            except h3.H3Error:
                return False
        return True

class OrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'product', 'product_name', 'quantity', 'price_at_purchase']
        read_only_fields = ['price_at_purchase']

class OrderLocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderLocation
        fields = ['id', 'name', 'district', 'lat', 'lng', 'description', 'unique_identity', 'is_system_defined', 'user']
        read_only_fields = ['user', 'is_system_defined']

class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True)
    location = serializers.PrimaryKeyRelatedField(queryset=OrderLocation.objects.all(), required=False, allow_null=True)
    shop_name = serializers.CharField(source='vendor.shop_name', read_only=True)

    class Meta:
        model = Order
        fields = ['id', 'vendor', 'shop_name', 'location', 'status', 'total_amount', 'delivery_fee', 'delivery_code', 'created_at', 'items']
        read_only_fields = ['status', 'delivery_code', 'created_at', 'total_amount', 'delivery_fee']

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        user = validated_data.pop('user', self.context['request'].user)

        # Calculate total amount
        total_amount = 0
        for item_data in items_data:
            product = item_data['product']
            total_amount += product.price * item_data['quantity']

        # Calculate Delivery Fee
        delivery_fee = 0
        order_location = validated_data.get('location')
        vendor = validated_data.get('vendor')
        
        if order_location and vendor:
            from multivender.services.pricing import calculate_delivery_fee
            try:
                delivery_fee = calculate_delivery_fee(vendor, order_location, subtotal=total_amount)
            except ValueError as e:
                raise serializers.ValidationError({"location": str(e)})

        total_amount += delivery_fee

        # Create Order
        order = Order.objects.create(user=user, total_amount=total_amount, delivery_fee=delivery_fee, **validated_data)

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
