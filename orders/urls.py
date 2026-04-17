from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import OrderLocationViewSet, OrderViewSet

router = DefaultRouter()
# Registers the endpoints:
# GET /api/orders/locations/ -> Fetches user & system locations
# POST /api/orders/locations/ -> Creates a new location for the logged-in user
router.register(r'locations', OrderLocationViewSet, basename='order-location')

# Registers the order endpoints:
# GET /api/orders/ -> Fetches all orders for the logged-in user
# POST /api/orders/ -> Creates a new order with items
# GET /api/orders/{id}/ -> Fetches details of a specific order
router.register(r'', OrderViewSet, basename='order')

urlpatterns = [
    path('', include(router.urls)),
]
