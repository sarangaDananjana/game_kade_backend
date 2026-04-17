from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ProductViewSet, FeaturedViewSet

router = DefaultRouter()
# Registers /api/products/featured/
router.register(r'featured', FeaturedViewSet, basename='featured')
router.register(r'', ProductViewSet)  # Registers /api/products/

urlpatterns = [
    path('', include(router.urls)),
]
