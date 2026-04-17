from rest_framework import viewsets, serializers
from rest_framework.permissions import IsAuthenticated
from django.db import models
from .models import OrderLocation, Order, OrderItem


class OrderLocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderLocation
        fields = '__all__'
        # Ensure users can't override these values via the API when creating a location
        read_only_fields = ('user', 'is_system_defined')


class OrderLocationViewSet(viewsets.ModelViewSet):
    serializer_class = OrderLocationSerializer
    permission_classes = [IsAuthenticated]  # Requires valid JWT Access Token

    def get_queryset(self):
        """
        GET API: Fetches system-defined locations AND the authenticated user's custom locations.
        """
        user = self.request.user
        return OrderLocation.objects.filter(
            models.Q(is_system_defined=True) | models.Q(user=user)
        )

    def perform_create(self, serializer):
        """
        POST API: When a user creates a location, it automatically assigns their user ID
        and forces 'is_system_defined' to False.
        """
        serializer.save(user=self.request.user, is_system_defined=False)


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ['product', 'quantity', 'price_at_purchase']


class OrderSerializer(serializers.ModelSerializer):
    # Handle nested items automatically
    items = OrderItemSerializer(many=True)

    class Meta:
        model = Order
        fields = ['id', 'location', 'status', 'total_amount',
                  'delivery_code', 'created_at', 'items']
        # Read-only so users can't override these on creation
        read_only_fields = ['id', 'status', 'delivery_code', 'created_at']

    def create(self, validated_data):
        # Extract the nested items data
        items_data = validated_data.pop('items')
        # Get the authenticated user from the view's context
        user = self.context['request'].user

        # Create the main order
        order = Order.objects.create(user=user, **validated_data)

        # Create all the nested order items
        for item_data in items_data:
            OrderItem.objects.create(order=order, **item_data)

        return order


class OrderViewSet(viewsets.ModelViewSet):
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Users can only view their own orders, ordered by newest first
        return Order.objects.filter(user=self.request.user).order_by('-created_at')
