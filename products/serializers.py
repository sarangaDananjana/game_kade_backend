from decimal import Decimal

from rest_framework import serializers

from .models import Featured, Product
from vendors.permissions import is_platform_admin


class ProductSerializer(serializers.ModelSerializer):
    price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.00'))

    class Meta:
        model = Product
        fields = ['id', 'vendor', 'name', 'category', 'description', 'price', 'image',
                  'stock_quantity', 'is_available', 'created_at']
        read_only_fields = ['created_at']

    def validate_vendor(self, vendor):
        request = self.context.get('request')
        if request and request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if not is_platform_admin(request.user) and vendor.owner_id != request.user.pk:
                raise serializers.ValidationError('You can manage meals only for your own kitchen.')
        return vendor


class FeaturedSerializer(serializers.ModelSerializer):
    class Meta:
        model = Featured
        fields = '__all__'
