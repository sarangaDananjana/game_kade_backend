"""Compatibility aliases for the shared Meal Drop rider APIs."""

from orders.views import (
    GetOptimizedRouteAPIView as MultiVendorGetOptimizedRouteAPIView,
    OptimizeRouteAPIView as MultiVendorOptimizeRouteAPIView,
    RiderOrdersWithRankAPIView as MultiVendorRiderOrdersWithRankAPIView,
    RiderOrderViewSet as MultiVendorRiderOrderViewSet,
)
