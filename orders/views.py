from rest_framework import viewsets, serializers, status
from django.contrib.gis.db import models
from django.contrib.gis.geos import Polygon
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.views.generic import TemplateView
from django.conf import settings
from .models import OrderLocation, Order, OrderItem, DeliveryZone


class OrderLocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderLocation
        # Exclude the internal PostGIS point field so it doesn't clutter the JSON response
        exclude = ['location_point']
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

    def to_representation(self, instance):
        """
        NEW: Override how the order is represented as JSON.
        Instead of returning just "location": 1, we embed the entire OrderLocation JSON.
        """
        response = super().to_representation(instance)
        # Check if a location is attached, then serialize it
        if instance.location:
            response['location'] = OrderLocationSerializer(
                instance.location).data
        return response

    def create(self, validated_data):
        # Extract the nested items data
        items_data = validated_data.pop('items')
        user = self.context['request'].user

        # --- NEW LOGIC: FIND THE ZONE ---
        location = validated_data.get('location')
        assigned_zone = None

        # Check which DeliveryZone polygon contains the order's Point
        if location and location.location_point:
            assigned_zone = DeliveryZone.objects.filter(
                polygon__contains=location.location_point,
                is_active=True
            ).first()

        # Create the main order WITH the assigned zone
        order = Order.objects.create(
            user=user,
            zone=assigned_zone,  # <-- Assign it here
            **validated_data
        )

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

    def update(self, request, *args, **kwargs):
        """
        PUT /api/orders/{id}/
        We override the default update method so that this endpoint is strictly
        used for cancelling an order by the user.
        """
        order = self.get_object()

        # Prevent cancelling orders that are already being prepared or delivered
        if order.status != 'pending':
            return Response(
                {'error': 'Only pending orders can be cancelled.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Update status to cancelled
        order.status = 'cancelled'
        order.save()

        # Return the updated order details
        serializer = self.get_serializer(order)
        return Response(serializer.data, status=status.HTTP_200_OK)


##################################################################################### Rider APP APIs ###############################################################

class IsRiderPermission(IsAuthenticated):
    """Custom permission to check if the user is a rider."""

    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.role == 'rider'


class RiderOrderViewSet(viewsets.ModelViewSet):
    serializer_class = OrderSerializer
    permission_classes = [IsRiderPermission]  # Only riders can access this

    def get_queryset(self):
        # Return only orders assigned to THIS specific rider
        # Exclude 'delivered' and 'cancelled' so their screen isn't cluttered
        return Order.objects.filter(
            rider=self.request.user
        ).exclude(status__in=['delivered', 'cancelled']).order_by('created_at')

    def update(self, request, *args, **kwargs):
        """
        Allow riders to update the status of their assigned orders 
        (e.g., from 'preparing' to 'out_for_delivery' to 'delivered').
        """
        order = self.get_object()
        new_status = request.data.get('status')

        valid_rider_statuses = ['out_for_delivery', 'delivered']

        if new_status not in valid_rider_statuses:
            return Response(
                {'error': 'Invalid status update.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Optional: Require them to pass the delivery_code to mark as delivered
        if new_status == 'delivered':
            provided_code = request.data.get('delivery_code')
            if str(order.delivery_code) != provided_code:
                return Response({'error': 'Invalid delivery code.'}, status=status.HTTP_400_BAD_REQUEST)

        order.status = new_status
        order.save()

        return Response(self.get_serializer(order).data)


##################################################################################### Delivery Zone API & Views ###############################################################

class DeliveryZoneSerializer(serializers.ModelSerializer):
    # To handle Polygon to GeoJSON and vice versa manually since we don't have rest_framework_gis
    coordinates = serializers.SerializerMethodField()

    class Meta:
        model = DeliveryZone
        fields = ['id', 'name', 'is_active', 'is_primary', 'coordinates']

    def get_coordinates(self, obj):
        if obj.polygon:
            # Returns a list of coordinates. E.g. ((x,y), (x,y), (x,y))
            # Convert to list of dicts for frontend {lat: y, lng: x}
            coords = obj.polygon.coords[0] # assuming single outer polygon
            return [{"lat": c[1], "lng": c[0]} for c in coords]
        return []


class DeliveryZoneViewSet(viewsets.ModelViewSet):
    queryset = DeliveryZone.objects.all()
    serializer_class = DeliveryZoneSerializer
    permission_classes = [AllowAny]  # No auth for now, as requested

    def create(self, request, *args, **kwargs):
        data = request.data
        name = data.get('name')
        is_primary = data.get('is_primary', False)
        coords_data = data.get('coordinates', []) 
        
        if coords_data and len(coords_data) >= 3:
            # coords_data expects: [{"lat": x, "lng": y}, ...]
            # Polygon expects list of (lng, lat) tuples
            poly_coords = [(c['lng'], c['lat']) for c in coords_data]
            
            # Close the polygon if not closed
            if poly_coords[0] != poly_coords[-1]:
                poly_coords.append(poly_coords[0])
                
            polygon = Polygon(poly_coords)
            
            # If this is marked as primary, un-mark existing primary zones
            if is_primary:
                DeliveryZone.objects.filter(is_primary=True).update(is_primary=False)
                
            zone = DeliveryZone.objects.create(name=name, polygon=polygon, is_primary=is_primary)
            return Response(DeliveryZoneSerializer(zone).data, status=status.HTTP_201_CREATED)
            
        return Response({'error': 'Invalid coordinates provided. Must provide at least 3 points.'}, status=status.HTTP_400_BAD_REQUEST)


class DeliveryZoneAdminView(TemplateView):
    template_name = 'orders/delivery_zone_map.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['google_maps_api_key'] = getattr(settings, 'GOOGLE_MAPS_API_KEY', '')
        return context
