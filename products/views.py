from rest_framework import viewsets, serializers
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from .models import Product, Featured

# Serializer formats the model data into JSON


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = '__all__'

# ViewSet handles GET, POST, PUT, DELETE automatically


class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    # Anyone can view products, but only authenticated users (or admins) can modify
    permission_classes = [IsAuthenticatedOrReadOnly]


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
