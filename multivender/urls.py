from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    VendorListView, VendorDetailView, OrderCreateView, 
    OrderListView, OrderDetailView, OrderLocationViewSet, 
    multivendor_web_view
)

router = DefaultRouter()
router.register(r'locations', OrderLocationViewSet, basename='location')

urlpatterns = [
    # REST API Endpoints
    path('vendors/', VendorListView.as_view(), name='vendor-list'),
    path('vendors/<int:pk>/', VendorDetailView.as_view(), name='vendor-detail'),
    path('orders/create/', OrderCreateView.as_view(), name='order-create'),
    path('orders/', OrderListView.as_view(), name='order-list'),
    path('orders/<int:pk>/', OrderDetailView.as_view(), name='order-detail'),
    
    # Web View Endpoint
    path('web/', multivendor_web_view, name='multivendor-web'),
] + router.urls
