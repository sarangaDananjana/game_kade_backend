from django.urls import path
from .views import OrderLocationViewSet, OrderViewSet, RiderOrderViewSet

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
]
