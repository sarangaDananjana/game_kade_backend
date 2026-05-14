from rest_framework import viewsets, serializers, status
from django.contrib.gis.db import models
from django.contrib.gis.geos import Polygon
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.views.generic import TemplateView
from django.conf import settings
from .models import OrderLocation, Order, OrderItem, DeliveryZone

import pytz
from django.utils import timezone
from rest_framework.views import APIView
from django.db.models import Sum, F
from users.models import CustomUser


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


# ─── Rider-specific serializers (do NOT touch user-app serializers above) ───────

class RiderOrderItemSerializer(serializers.ModelSerializer):
    """Extends the base item serializer to include the human-readable product name."""
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = OrderItem
        fields = ['product', 'product_name', 'quantity', 'price_at_purchase']


class RiderOrderLocationSerializer(serializers.ModelSerializer):
    """Location serializer for riders — exposes `landmark` instead of the raw `unique_identity` field name."""
    landmark = serializers.CharField(source='unique_identity', read_only=True)

    class Meta:
        model = OrderLocation
        fields = ['id', 'name', 'lat', 'lng', 'description', 'landmark']


class RiderOrderSerializer(serializers.ModelSerializer):
    """Full order serializer for the rider app — includes item names, customer contact, and clean location data."""
    items = RiderOrderItemSerializer(many=True, read_only=True)
    location = RiderOrderLocationSerializer(read_only=True)
    customer_name = serializers.CharField(source='user.name', read_only=True)
    customer_phone = serializers.CharField(source='user.phone_number', read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'status', 'total_amount', 'delivery_code', 'created_at',
            'customer_name', 'customer_phone', 'location', 'items'
        ]


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
    serializer_class = RiderOrderSerializer
    permission_classes = [IsRiderPermission]  # Only riders can access this

    def get_queryset(self):
        # For list action: exclude delivered/cancelled so the screen isn't cluttered
        # For update actions: include all non-cancelled orders so status transitions work
        if self.action in ['update', 'partial_update']:
            return Order.objects.filter(
                rider=self.request.user
            ).exclude(status='cancelled').order_by('created_at')
        return Order.objects.filter(
            rider=self.request.user
        ).exclude(status__in=['delivered', 'cancelled']).order_by('created_at')

    def update(self, request, *args, **kwargs):
        """
        Allow riders to update the status of their assigned orders 
        (e.g., from 'preparing' to 'out_for_delivery' to 'delivered').
        """
        try:
            order = self.get_object()
        except Exception:
            return Response(
                {'error': 'Order not found or not assigned to you.'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        new_status = request.data.get('status')

        valid_rider_statuses = ['out_for_delivery', 'delivered']

        if new_status not in valid_rider_statuses:
            return Response(
                {'error': f'Invalid status update. Valid statuses: {valid_rider_statuses}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Prevent re-delivering already delivered orders
        if order.status == 'delivered':
            return Response(
                {'error': 'Order has already been delivered.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Require delivery_code to mark as delivered
        if new_status == 'delivered':
            provided_code = request.data.get('delivery_code')
            if not provided_code or str(order.delivery_code) != str(provided_code).strip():
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


##################################################################################### Daily Assignment API & Views ###############################################################

class DailySummaryAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        colombo_tz = pytz.timezone('Asia/Colombo')
        now = timezone.now().astimezone(colombo_tz)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)

        summary = OrderItem.objects.filter(
            order__created_at__range=(today_start, today_end)
        ).values(
            product_name=F('product__name')
        ).annotate(
            total_quantity=Sum('quantity')
        ).order_by('-total_quantity')

        return Response(list(summary))


class UnassignedOrdersAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        colombo_tz = pytz.timezone('Asia/Colombo')
        now = timezone.now().astimezone(colombo_tz)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)

        orders = Order.objects.filter(
            created_at__range=(today_start, today_end),
            rider__isnull=True
        ).exclude(status__in=['cancelled', 'delivered'])
        
        result = []
        for o in orders:
            lat = o.location.lat if o.location else None
            lng = o.location.lng if o.location else None
            if lat is not None and lng is not None:
                result.append({
                    'id': o.id,
                    'lat': lat,
                    'lng': lng,
                    'status': o.status,
                    'total_amount': o.total_amount,
                })
        return Response(result)


class BulkAssignRiderAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        rider_id = request.data.get('rider_id')
        order_ids = request.data.get('order_ids', [])

        if not rider_id or not order_ids:
            return Response({'error': 'rider_id and order_ids are required.'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            rider = CustomUser.objects.get(id=rider_id, role='rider')
        except CustomUser.DoesNotExist:
            return Response({'error': 'Invalid rider.'}, status=status.HTTP_400_BAD_REQUEST)

        updated_count = Order.objects.filter(id__in=order_ids).update(rider=rider)
        
        return Response({'message': f'Assigned {updated_count} orders to rider.'})


class RidersListAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        riders = CustomUser.objects.filter(role='rider', is_active=True).values('id', 'name', 'phone_number')
        return Response(list(riders))


class DailyAssignmentAdminView(TemplateView):
    template_name = 'orders/daily_assignment.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['google_maps_api_key'] = getattr(settings, 'GOOGLE_MAPS_API_KEY', '')
        return context

class RiderOrdersWithRankAPIView(APIView):
    """
    GET /api/orders/rider/orders/with-rank/

    Returns today's active orders for the authenticated rider, each enriched with
    a `rank` field pulled from MongoDB route_states.

    - If no route has been optimized yet, every order returns `"rank": null`.
    - Orders are sorted: ranked orders first (by rank asc), then unranked.
    """
    permission_classes = [IsRiderPermission]

    def get(self, request, *args, **kwargs):
        colombo_tz = pytz.timezone('Asia/Colombo')
        now = timezone.now().astimezone(colombo_tz)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        today_str = now.strftime('%Y-%m-%d')

        # 1. Fetch today's active orders from PostgreSQL
        orders = Order.objects.filter(
            rider=request.user,
            created_at__range=(today_start, today_end)
        ).exclude(status__in=['delivered', 'cancelled']).order_by('created_at')

        # 2. Try to load today's rank map from MongoDB
        rank_map = {}  # { order_id (int): rank (int) }
        mongo_uri = getattr(settings, 'MONGO_URI', None)
        if mongo_uri:
            try:
                from pymongo import MongoClient
                client = MongoClient(mongo_uri)
                db = client['Game_Kade']
                collection = db['route_states']
                saved_doc = collection.find_one(
                    {'rider_id': request.user.id, 'date': today_str},
                    {'_id': 0}
                )
                if saved_doc:
                    for point in saved_doc.get('route_points', []):
                        rank_map[point['order_id']] = point['rank']
            except Exception:
                # MongoDB unavailable — just return unranked orders, don't crash
                pass

        # 3. Serialize orders and inject rank
        serializer = RiderOrderSerializer(orders, many=True)
        result = []
        for order_data in serializer.data:
            order_id = order_data['id']
            enriched = dict(order_data)
            enriched['rank'] = rank_map.get(order_id, None)
            result.append(enriched)

        # 4. Sort: ranked first (ascending), unranked last
        result.sort(key=lambda x: (x['rank'] is None, x['rank'] or 0))

        return Response(result, status=status.HTTP_200_OK)


class OptimizeRouteAPIView(APIView):
    permission_classes = [IsRiderPermission]

    def post(self, request, *args, **kwargs):
        # 1. Get rider's current lat/lng from request body
        lat = request.data.get('lat')
        lng = request.data.get('lng')

        if not lat or not lng:
            return Response({'error': 'Latitude (lat) and Longitude (lng) are required.'}, status=status.HTTP_400_BAD_REQUEST)

        origin_str = f"{lat},{lng}"

        # 2. Get today's orders for this rider
        colombo_tz = pytz.timezone('Asia/Colombo')
        now = timezone.now().astimezone(colombo_tz)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)

        orders = Order.objects.filter(
            rider=request.user,
            created_at__range=(today_start, today_end)
        ).exclude(status__in=['delivered', 'cancelled'])

        if not orders.exists():
            return Response({'error': 'No active orders found for today.'}, status=status.HTTP_404_NOT_FOUND)

        # 3. Extract coordinates and cluster by identical locations
        # Dictionary to map "lat,lng" to a list of order IDs
        location_clusters = {}
        waypoints = []

        for o in orders:
            if o.location and o.location.lat is not None and o.location.lng is not None:
                coord_str = f"{o.location.lat},{o.location.lng}"
                if coord_str not in location_clusters:
                    location_clusters[coord_str] = []
                    waypoints.append(coord_str)
                location_clusters[coord_str].append(o.id)

        if not waypoints:
            return Response({'error': 'None of the assigned orders have valid coordinates.'}, status=status.HTTP_400_BAD_REQUEST)

        if len(waypoints) > 25:
            return Response({'error': 'Too many unique waypoints for Google Directions API (max 25).'}, status=status.HTTP_400_BAD_REQUEST)

        # 4. Call Google Maps Directions API
        api_key = getattr(settings, 'GOOGLE_MAPS_API_KEY', '')
        waypoints_str = "optimize:true|" + "|".join(waypoints)
        
        # Set destination=origin to get them back to where they started (round trip)
        url = "https://maps.googleapis.com/maps/api/directions/json"
        params = {
            'origin': origin_str,
            'destination': origin_str,
            'waypoints': waypoints_str,
            'key': api_key
        }

        import requests
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            return Response({'error': f"Failed to reach Google Maps API: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if data.get('status') != 'OK':
            return Response({'error': f"Google Maps API error: {data.get('status')} - {data.get('error_message', '')}"}, status=status.HTTP_400_BAD_REQUEST)

        # 5. Extract optimized sequence
        waypoint_order = data['routes'][0].get('waypoint_order', [])
        
        # Build route_points array with ranks
        route_points = []
        for optimized_rank, original_index in enumerate(waypoint_order):
            coord_str = waypoints[original_index]
            order_ids = location_clusters[coord_str]
            # All orders at this coordinate share the same rank
            for order_id in order_ids:
                lat_str, lng_str = coord_str.split(',')
                route_points.append({
                    'order_id': order_id,
                    'lat': float(lat_str),
                    'lng': float(lng_str),
                    'rank': optimized_rank + 1
                })

        # 6. Save to MongoDB
        mongo_uri = getattr(settings, 'MONGO_URI', None)
        if not mongo_uri:
            return Response({'error': 'MONGO_URI not configured.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        try:
            from pymongo import MongoClient
            client = MongoClient(mongo_uri)
            db = client['Game_Kade']
            collection = db['route_states']

            document = {
                'rider_id': request.user.id,
                'rider_name': request.user.name,
                'rider_number': request.user.phone_number,
                'date': now.strftime('%Y-%m-%d'),
                'route_points': route_points
            }

            # Update if exists for this rider and date, otherwise insert
            result = collection.update_one(
                {'rider_id': request.user.id, 'date': now.strftime('%Y-%m-%d')},
                {'$set': document},
                upsert=True
            )

            # Retrieve the final document to return (without _id or stringified _id)
            saved_doc = collection.find_one({'rider_id': request.user.id, 'date': now.strftime('%Y-%m-%d')}, {'_id': 0})
            
            return Response(saved_doc, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({'error': f"MongoDB error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class GetOptimizedRouteAPIView(APIView):
    permission_classes = [IsRiderPermission]

    def get(self, request, *args, **kwargs):
        colombo_tz = pytz.timezone('Asia/Colombo')
        now = timezone.now().astimezone(colombo_tz)
        today_str = now.strftime('%Y-%m-%d')

        mongo_uri = getattr(settings, 'MONGO_URI', None)
        if not mongo_uri:
            return Response({'error': 'MONGO_URI not configured.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        try:
            from pymongo import MongoClient
            client = MongoClient(mongo_uri)
            db = client['Game_Kade']
            collection = db['route_states']

            # Find document for THIS rider and TODAY
            saved_doc = collection.find_one(
                {'rider_id': request.user.id, 'date': today_str},
                {'_id': 0}
            )

            if not saved_doc:
                return Response({'error': 'No optimized route found for today.'}, status=status.HTTP_404_NOT_FOUND)

            return Response(saved_doc, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({'error': f"MongoDB error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
