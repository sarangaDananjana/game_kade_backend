from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .delivery_quotes import BasketDeliveryQuoteView
from .rider_views import (
    MultiVendorGetOptimizedRouteAPIView, MultiVendorOptimizeRouteAPIView,
    MultiVendorRiderOrdersWithRankAPIView, MultiVendorRiderOrderViewSet,
)
from .views import (
    VendorListView, VendorDetailView, OrderCreateView, 
    OrderListView, OrderDetailView, OrderLocationViewSet, 
    multivendor_web_view,
    admin_dashboard_view, admin_login_view, admin_logout_view,
    admin_orders_api, admin_assign_orders_api, admin_unassign_orders_api
)

router = DefaultRouter()
router.register(r'locations', OrderLocationViewSet, basename='location')

urlpatterns = [
    # REST API Endpoints
    path('vendors/', VendorListView.as_view(), name='vendor-list'),
    path('vendors/<int:pk>/', VendorDetailView.as_view(), name='vendor-detail'),
    path('orders/create/', OrderCreateView.as_view(), name='order-create'),
    path('orders/delivery-quote/', BasketDeliveryQuoteView.as_view(), name='basket-delivery-quote'),
    path('orders/', OrderListView.as_view(), name='order-list'),
    path('orders/<int:pk>/', OrderDetailView.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update'}), name='order-detail'),
    path('rider/orders/', MultiVendorRiderOrderViewSet.as_view({'get': 'list'}), name='multivendor-rider-orders'),
    path('rider/orders/with-rank/', MultiVendorRiderOrdersWithRankAPIView.as_view(), name='multivendor-rider-orders-with-rank'),
    path('rider/orders/<int:pk>/', MultiVendorRiderOrderViewSet.as_view({'put': 'update', 'patch': 'update'}), name='multivendor-rider-order-detail'),
    path('rider/routes/optimize/', MultiVendorOptimizeRouteAPIView.as_view(), name='multivendor-rider-optimize-route'),
    path('rider/routes/current/', MultiVendorGetOptimizedRouteAPIView.as_view(), name='multivendor-rider-current-route'),
    
    # Customer Web View Endpoint
    path('web/', multivendor_web_view, name='multivendor-web'),
    
    # Custom Multi-Vendor Admin Dashboard & Rider Assignment
    path('admin/dashboard/', admin_dashboard_view, name='multivendor-admin-dashboard'),
    path('admin/login/', admin_login_view, name='multivendor-admin-login'),
    path('admin/logout/', admin_logout_view, name='multivendor-admin-logout'),
    path('admin/api/orders/', admin_orders_api, name='multivendor-admin-api-orders'),
    path('admin/api/assign/', admin_assign_orders_api, name='multivendor-admin-api-assign'),
    path('admin/api/unassign/', admin_unassign_orders_api, name='multivendor-admin-api-unassign'),
] + router.urls
