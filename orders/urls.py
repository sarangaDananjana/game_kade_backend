from django.urls import path
from .views import (
    OrderLocationViewSet, OrderViewSet, RiderOrderViewSet, DeliveryZoneViewSet, DeliveryZoneAdminView,
    DailySummaryAPIView, UnassignedOrdersAPIView, BulkAssignRiderAPIView, RidersListAPIView, DailyAssignmentAdminView
)

urlpatterns = [
    # Order Locations Endpoints
    path('locations/', OrderLocationViewSet.as_view({
        'get': 'list',
        'post': 'create'
    }), name='order-location-list'),

    path('locations/<int:pk>/', OrderLocationViewSet.as_view({
        'get': 'retrieve',
        'put': 'update',
        'patch': 'partial_update',
        'delete': 'destroy'
    }), name='order-location-detail'),

    # Orders Endpoints
    path('', OrderViewSet.as_view({
        'get': 'list',
        'post': 'create'
    }), name='order-list'),

    path('<int:pk>/', OrderViewSet.as_view({
        'get': 'retrieve',
        'put': 'update',
        'patch': 'partial_update',
        'delete': 'destroy'
    }), name='order-detail'),

    ###################################################### RIDER APP ENDPOINTS ############################################################

    path('rider/orders/',
         RiderOrderViewSet.as_view({'get': 'list'}), name='rider-orders-list'),
    path('rider/orders/<int:pk>/', RiderOrderViewSet.as_view(
        {'put': 'update', 'patch': 'update'}), name='rider-orders-detail'),

    ###################################################### DELIVERY ZONE MAP APP ############################################################
    
    path('delivery-zones/', DeliveryZoneViewSet.as_view({
        'get': 'list',
        'post': 'create'
    }), name='delivery-zones-list'),
    
    path('admin/delivery-zones/map/', DeliveryZoneAdminView.as_view(), name='delivery-zone-map-admin'),

    ###################################################### DAILY ASSIGNMENT MAP APP ############################################################
    
    path('daily-summary/', DailySummaryAPIView.as_view(), name='daily-summary'),
    path('today-unassigned/', UnassignedOrdersAPIView.as_view(), name='today-unassigned'),
    path('bulk-assign/', BulkAssignRiderAPIView.as_view(), name='bulk-assign'),
    path('riders/', RidersListAPIView.as_view(), name='riders-list'),
    path('admin/daily-assignment/map/', DailyAssignmentAdminView.as_view(), name='daily-assignment-admin'),
]
