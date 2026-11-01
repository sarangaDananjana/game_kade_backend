from django.db.models import Q
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.exceptions import ValidationError
from .models import Product, Featured
from .serializers import ProductSerializer, FeaturedSerializer
from vendors.permissions import CanManageProducts, is_platform_admin

# Serializer formats the model data into JSON


class ProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    permission_classes = [CanManageProducts]

    def get_queryset(self):
        """
        Optionally restricts the returned products to a given category,
        by filtering against a `category` query parameter in the URL.
        """
        queryset = Product.objects.select_related('vendor')
        user = self.request.user
        if not is_platform_admin(user):
            if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
                visibility = Q(vendor__is_active=True)
                if user.is_authenticated:
                    visibility |= Q(vendor__owner=user)
                queryset = queryset.filter(visibility)
            else:
                queryset = queryset.filter(vendor__owner=user)
        vendor = self.request.query_params.get('vendor')
        if vendor is not None:
            try:
                vendor = int(vendor)
            except (TypeError, ValueError):
                raise ValidationError({'vendor': 'Choose a valid kitchen.'})
            queryset = queryset.filter(vendor_id=vendor)
        category = self.request.query_params.get('category', None)
        if category is not None:
            queryset = queryset.filter(category=category)
        return queryset


# ReadOnlyModelViewSet only allows GET requests (list and retrieve)


class FeaturedViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = FeaturedSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        # Only fetch features where is_active is True
        return Featured.objects.filter(is_active=True)
