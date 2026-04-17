from django.urls import path
from .views import ProductViewSet, FeaturedViewSet

urlpatterns = [
    # Featured Endpoints
    path('featured/',
         FeaturedViewSet.as_view({'get': 'list'}), name='featured-list'),
    path('featured/<int:pk>/',
         FeaturedViewSet.as_view({'get': 'retrieve'}), name='featured-detail'),

    # Product Endpoints
    path('', ProductViewSet.as_view({
        'get': 'list',
        'post': 'create'
    }), name='product-list'),

    path('<int:pk>/', ProductViewSet.as_view({
        'get': 'retrieve',
        'put': 'update',
        'patch': 'partial_update',
        'delete': 'destroy'
    }), name='product-detail'),
]
