from rest_framework import viewsets, serializers
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from .models import Product

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
