from rest_framework import serializers

from products.serializers import ProductSerializer
from .models import Vendor

class VendorSerializer(serializers.ModelSerializer):
    products = serializers.SerializerMethodField()
    delivery_fee = serializers.SerializerMethodField()
    is_available = serializers.SerializerMethodField()

    class Meta:
        model = Vendor
        fields = ['id', 'shop_name', 'logo', 'cover_image', 'district', 'lat', 'lng', 'is_pickup_only', 'is_active', 'products', 'delivery_fee', 'is_available', 'h3_index']

    def get_products(self, obj):
        products = list(obj.products.all())
        return ProductSerializer(products, many=True, context=self.context).data

    def get_delivery_fee(self, obj):
        order_location = self.context.get('order_location')
        if order_location and obj.h3_index and order_location.h3_index:
            from orders.services.pricing import calculate_delivery_fee
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
            from orders.services.pricing import DeliveryPricingError, quote_delivery
            try:
                quote_delivery(obj, order_location)
                return True
            except DeliveryPricingError:
                return False
        return True
