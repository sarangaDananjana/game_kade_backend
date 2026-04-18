from rest_framework import viewsets, serializers
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from .models import Product, Featured

# Serializer formats the model data into JSON


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = '__all__'


class ProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    # Anyone can view products, but only authenticated users (or admins) can modify
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        """
        Optionally restricts the returned products to a given category,
        by filtering against a `category` query parameter in the URL.
        """
        queryset = Product.objects.all()
        category = self.request.query_params.get('category', None)
        if category is not None:
            queryset = queryset.filter(category=category)
        return queryset


class FeaturedSerializer(serializers.ModelSerializer):
    class Meta:
        model = Featured
        fields = '__all__'

# ReadOnlyModelViewSet only allows GET requests (list and retrieve)


class FeaturedViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = FeaturedSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        # Only fetch features where is_active is True
        return Featured.objects.filter(is_active=True)
