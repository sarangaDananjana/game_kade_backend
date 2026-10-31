"""Rider endpoints for the same multi-vendor orders shown in MealDrop."""

from orders.views import (
    GetOptimizedRouteAPIView, OptimizeRouteAPIView,
    RiderOrdersWithRankAPIView, RiderOrderViewSet,
)
from .models import Order
from .serializers import RiderOrderSerializer


class MultiVendorRiderOrderViewSet(RiderOrderViewSet):
    order_model = Order
    serializer_class = RiderOrderSerializer

    def get_queryset(self):
        return super().get_queryset().select_related('user', 'vendor', 'location').prefetch_related('items__product')


class MultiVendorRiderOrdersWithRankAPIView(RiderOrdersWithRankAPIView):
    order_model = Order
    serializer_class = RiderOrderSerializer
    route_collection = 'multivendor_route_states'
    select_related_fields = ('user', 'vendor', 'location')


class MultiVendorOptimizeRouteAPIView(OptimizeRouteAPIView):
    order_model = Order
    route_collection = 'multivendor_route_states'


class MultiVendorGetOptimizedRouteAPIView(GetOptimizedRouteAPIView):
    route_collection = 'multivendor_route_states'
