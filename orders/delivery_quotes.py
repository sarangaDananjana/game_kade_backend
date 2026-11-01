"""Read-only basket quotes using current products, location, and tier records."""

from decimal import Decimal

from rest_framework import permissions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import OrderLocation
from vendors.models import Vendor
from products.serializers import ProductSerializer
from .services.pricing import (
    DeliveryPricingError, load_delivery_tiers, minimum_spend_for_lower_total,
    money_text, next_delivery_saving, quote_delivery,
)


class QuoteItemInput(serializers.Serializer):
    product = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1, max_value=10000)


class QuoteOrderInput(serializers.Serializer):
    vendor = serializers.IntegerField(min_value=1)
    items = QuoteItemInput(many=True, allow_empty=False, max_length=100)


class BasketQuoteInput(serializers.Serializer):
    location = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    lat = serializers.FloatField(min_value=-90, max_value=90, required=False)
    lng = serializers.FloatField(min_value=-180, max_value=180, required=False)
    orders = QuoteOrderInput(many=True, allow_empty=False, max_length=20)

    def validate(self, attrs):
        if ('lat' in attrs) != ('lng' in attrs):
            raise serializers.ValidationError('Both latitude and longitude are required.')
        vendors = [order['vendor'] for order in attrs['orders']]
        if len(set(vendors)) != len(vendors):
            raise serializers.ValidationError('Combine each kitchen into a single order group.')
        return attrs


class BasketDeliveryQuoteView(APIView):
    # Anonymous coordinate quotes support browsing; saved addresses remain private.
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        payload = BasketQuoteInput(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        location = None
        if data.get('location') is not None:
            location = OrderLocation.objects.filter(pk=data['location']).first()
            if location is None:
                return Response({'detail': 'This delivery address is no longer available.', 'code': 'location_unavailable'}, status=404)
            if not location.is_system_defined and (not request.user.is_authenticated or location.user_id != request.user.pk):
                return Response({'detail': 'Choose one of your own delivery addresses.', 'code': 'location_not_allowed'}, status=403)
        coordinates = (data['lat'], data['lng']) if 'lat' in data else None
        vendors = {vendor.pk: vendor for vendor in Vendor.objects.filter(
            pk__in=[order['vendor'] for order in data['orders']],
        ).prefetch_related('products')}
        tiers = load_delivery_tiers()
        quotes = []
        for group in data['orders']:
            vendor = vendors.get(group['vendor'])
            if vendor is None:
                return Response({'detail': 'One of your kitchens is no longer available.', 'code': 'vendor_unavailable'}, status=404)
            products = {product.pk: product for product in vendor.products.all()}
            quantities = {}
            for item in group['items']:
                if item['product'] not in products:
                    return Response({'detail': 'A meal does not belong to its selected kitchen.', 'code': 'invalid_product'}, status=400)
                quantities[item['product']] = quantities.get(item['product'], 0) + item['quantity']
            parcel_count = sum(quantities.values())
            if parcel_count > 10000:
                return Response({'detail': 'Reduce the number of meals in this order.', 'code': 'too_many_parcels'}, status=400)
            subtotal = sum((products[pk].price * count for pk, count in quantities.items()), Decimal('0.00'))
            try:
                quote = quote_delivery(vendor, location, subtotal, parcel_count, coordinates=coordinates, tiers=tiers)
            except DeliveryPricingError as exc:
                return Response({'detail': str(exc), 'code': exc.code, 'vendor': vendor.pk}, status=422)
            issues = []
            if not vendor.is_active:
                issues.append('This kitchen is currently unavailable.')
            lines = []
            for pk, count in quantities.items():
                product = products[pk]
                if not product.is_available or product.stock_quantity < count:
                    issues.append(f'Update the quantity of {product.name}; only {product.stock_quantity if product.is_available else 0} are available.')
                lines.append({'product': ProductSerializer(product).data, 'quantity': count,
                              'line_total': money_text(product.price * count)})
            suggestion = next_delivery_saving(quote, list(products.values()), quantities, tiers) if not issues else None
            if suggestion:
                suggestion['product'] = ProductSerializer(suggestion['product']).data
            quote.update({
                'vendor': vendor.pk, 'shop_name': vendor.shop_name, 'is_pickup_only': vendor.is_pickup_only,
                'is_active': vendor.is_active, 'can_checkout': not issues, 'issues': issues,
                'items': lines, 'next_saving': suggestion,
                'minimum_extra_spend_for_lower_total': minimum_spend_for_lower_total(quote, tiers),
            })
            quotes.append(quote)
        return Response({
            'currency': 'LKR', 'discount_rate': '0.10', 'quotes': quotes,
            'subtotal': money_text(sum((Decimal(quote['subtotal']) for quote in quotes), Decimal('0.00'))),
            'delivery_fee': money_text(sum((Decimal(quote['delivery_fee']) for quote in quotes), Decimal('0.00'))),
            'total_amount': money_text(sum((Decimal(quote['total_amount']) for quote in quotes), Decimal('0.00'))),
            'delivery_discount': money_text(sum((Decimal(quote['delivery_discount']) for quote in quotes), Decimal('0.00'))),
            'can_checkout': all(quote['can_checkout'] for quote in quotes),
        })
