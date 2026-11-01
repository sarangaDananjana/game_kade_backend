from rest_framework import serializers
from .models import Vendor, Product, OrderLocation, Order, OrderItem
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as ModelValidationError
from django.db import transaction
from decimal import Decimal
from django.utils.functional import cached_property
from orders.service_area import DeliveryAreaError, check_delivery_area, primary_delivery_zone

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
        if not obj.is_active:
            return False
        if obj.is_pickup_only:
            return True
        if order_location:
            from .services.pricing import DeliveryPricingError, quote_delivery
            try:
                quote_delivery(obj, order_location)
                return True
            except DeliveryPricingError:
                return False
        return True

class OrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    quantity = serializers.IntegerField(min_value=1, max_value=10000)

    class Meta:
        model = OrderItem
        fields = ['id', 'product', 'product_name', 'quantity', 'price_at_purchase']
        read_only_fields = ['price_at_purchase']

class OrderLocationSerializer(serializers.ModelSerializer):
    delivery_available = serializers.SerializerMethodField()
    delivery_message = serializers.SerializerMethodField()

    class Meta:
        model = OrderLocation
        fields = ['id', 'name', 'district', 'lat', 'lng', 'description', 'unique_identity', 'is_system_defined', 'user', 'delivery_available', 'delivery_message']
        read_only_fields = ['user', 'is_system_defined']

    @cached_property
    def delivery_zone(self):
        return primary_delivery_zone()

    def delivery_error(self, obj):
        try:
            check_delivery_area(obj.lat, obj.lng, zone=self.delivery_zone)
        except DeliveryAreaError as exc:
            return str(exc)
        return None

    def get_delivery_available(self, obj):
        return self.delivery_error(obj) is None

    def get_delivery_message(self, obj):
        return self.delivery_error(obj)

    def validate(self, attrs):
        lat = attrs.get('lat', getattr(self.instance, 'lat', None))
        lng = attrs.get('lng', getattr(self.instance, 'lng', None))
        try:
            zone = check_delivery_area(lat, lng, zone=self.delivery_zone)
        except DeliveryAreaError as exc:
            raise serializers.ValidationError({'detail': str(exc), 'code': exc.code}) from exc
        attrs['district'] = zone.district
        return attrs

class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True)
    location = serializers.PrimaryKeyRelatedField(queryset=OrderLocation.objects.all(), required=False, allow_null=True)
    location_name = serializers.CharField(source='location.name', read_only=True, allow_null=True)
    shop_name = serializers.CharField(source='vendor.shop_name', read_only=True)
    subtotal = serializers.SerializerMethodField()
    delivery_pin = serializers.CharField(read_only=True)
    qr_payload = serializers.CharField(source='delivery_code', read_only=True)
    expected_total_amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.00'), required=False, write_only=True)

    class Meta:
        model = Order
        fields = ['id', 'vendor', 'shop_name', 'location', 'location_name', 'status', 'subtotal', 'total_amount', 'delivery_fee', 'delivery_code', 'delivery_pin', 'qr_payload', 'expected_total_amount', 'created_at', 'items']
        read_only_fields = ['status', 'delivery_code', 'created_at', 'total_amount', 'delivery_fee', 'subtotal']

    def get_subtotal(self, obj):
        return sum(item.price_at_purchase * item.quantity for item in obj.items.all())

    def validate_items(self, items):
        if not items or len(items) > 100 or sum(item['quantity'] for item in items) > 10000:
            raise serializers.ValidationError('Choose between 1 and 10,000 meals in this order.')
        return items

    def validate_location(self, location):
        if location is not None and not location.is_system_defined and location.user_id != self.context['request'].user.pk:
            raise serializers.ValidationError('Choose one of your own delivery addresses.')
        return location

    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop('items')
        expected_total = validated_data.pop('expected_total_amount', None)
        user = validated_data.pop('user', self.context['request'].user)

        vendor = validated_data['vendor']
        if not vendor.is_active:
            raise serializers.ValidationError({'vendor': 'This kitchen is currently unavailable.'})
        quantities = {}
        for item in items_data:
            product = item['product']
            quantities[product.pk] = quantities.get(product.pk, 0) + item['quantity']
        products = {product.pk: product for product in Product.objects.select_for_update().filter(pk__in=quantities, vendor=vendor)}
        if len(products) != len(quantities):
            raise serializers.ValidationError({'items': 'A meal does not belong to its selected kitchen.'})
        for pk, count in quantities.items():
            if not products[pk].is_available or count > products[pk].stock_quantity:
                raise serializers.ValidationError({'items': 'Some meals are no longer available in the quantity selected.'})
        items_data = [{'product': products[pk], 'quantity': count} for pk, count in quantities.items()]

        # Calculate items subtotal
        subtotal = sum(item_data['product'].price * item_data['quantity'] for item_data in items_data)

        # Calculate Delivery Fee
        order_location = validated_data.get('location')
        vendor = validated_data.get('vendor')
        
        # Fallback to user's saved location if not provided
        if not order_location and not vendor.is_pickup_only:
            order_location = OrderLocation.objects.filter(user=user).order_by('-id').first()
            if order_location:
                validated_data['location'] = order_location

        if not order_location and not vendor.is_pickup_only:
            raise serializers.ValidationError({"location": "A delivery location is required to calculate delivery charge and place an order."})

        delivery_fee = 0
        if vendor:
            from multivender.services.pricing import calculate_delivery_fee
            try:
                delivery_fee = calculate_delivery_fee(vendor, order_location, subtotal=subtotal, parcel_count=sum(quantities.values()))
            except ValueError as e:
                raise serializers.ValidationError({"location": str(e)})

        total_amount = subtotal + delivery_fee
        if expected_total is not None and total_amount != expected_total:
            raise serializers.ValidationError({'expected_total_amount': 'Prices or delivery charges changed. Refresh your basket before ordering.'}, code='price_changed')

        # Create Order
        try:
            order = Order.objects.create(user=user, total_amount=total_amount, delivery_fee=delivery_fee, **validated_data)
        except ModelValidationError as exc:
            raise serializers.ValidationError({'delivery_code': exc.messages}) from exc

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


class RiderOrderLocationSerializer(serializers.ModelSerializer):
    landmark = serializers.CharField(source='unique_identity', read_only=True)

    class Meta:
        model = OrderLocation
        fields = ['id', 'name', 'lat', 'lng', 'description', 'landmark', 'is_system_defined', 'user']


class RiderOrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    location = RiderOrderLocationSerializer(read_only=True)
    customer_name = serializers.CharField(source='user.name', read_only=True)
    customer_phone = serializers.CharField(source='user.phone_number', read_only=True)
    shop_name = serializers.CharField(source='vendor.shop_name', read_only=True)
    delivery_pin = serializers.CharField(read_only=True)
    qr_payload = serializers.CharField(source='delivery_code', read_only=True)

    class Meta:
        model = Order
        fields = ['id', 'vendor', 'shop_name', 'status', 'total_amount', 'delivery_fee',
                  'delivery_code', 'delivery_pin', 'qr_payload', 'created_at',
                  'customer_name', 'customer_phone', 'location', 'items']
