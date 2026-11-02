from rest_framework import generics, viewsets, status
from django.contrib.gis.db import models
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from django.views.generic import TemplateView
from django.conf import settings
from django.db import transaction
from django.http import Http404
from .models import OrderLocation, Order, OrderItem, DeliveryZone
from .service_area import DeliveryAreaError, check_delivery_area
from .services.delivery_codes import delivery_code_matches
from .serializers import (
    DeliveryZoneCreateSerializer, DeliveryZoneSerializer, OrderLocationSerializer,
    OrderSerializer, RiderOrderSerializer,
)
from rest_framework.exceptions import ValidationError
from zoneinfo import ZoneInfo
from django.contrib.auth.mixins import UserPassesTestMixin
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication
from vendors.models import Vendor
from vendors.permissions import CanManageDeliveryZones, IsPlatformAdmin, is_platform_admin
from .service_area import primary_delivery_zone
from .services.assignment import AssignmentError, assign_orders

import pytz
from django.utils import timezone
from rest_framework.views import APIView
from django.db.models import Sum, F
from users.models import CustomUser


class OrderLocationViewSet(viewsets.ModelViewSet):
    serializer_class = OrderLocationSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        user = self.request.user
        if self.action in ('update', 'partial_update', 'destroy'):
            return OrderLocation.objects.filter(user=user, is_system_defined=False)
        if user.is_authenticated:
            return OrderLocation.objects.filter(models.Q(is_system_defined=True) | models.Q(user=user))
        return OrderLocation.objects.filter(is_system_defined=True)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, is_system_defined=False)


class CheckoutHoursMixin:
    def perform_create(self, serializer):
        local_time = timezone.now().astimezone(ZoneInfo('Asia/Colombo'))
        if not (12 <= local_time.hour < 17):
            raise ValidationError('We are currently closed. Orders are only accepted between 12:00 PM and 5:00 PM.')
        serializer.save(user=self.request.user)


class OrderViewSet(CheckoutHoursMixin, viewsets.ModelViewSet):
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = Order.objects.filter(user=self.request.user).select_related('vendor', 'location').prefetch_related('items__product')
        if self.action in ('update', 'partial_update'):
            queryset = queryset.select_for_update(of=('self',))
        return queryset.order_by('-created_at')

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        order = self.get_object()
        if request.data.get('status') != 'cancelled':
            return Response({'error': 'You can only cancel pending orders.'}, status=status.HTTP_400_BAD_REQUEST)
        if order.status != 'pending':
            return Response({'error': 'Only pending orders can be cancelled.'}, status=status.HTTP_400_BAD_REQUEST)
        order.status = 'cancelled'
        order.save(update_fields=['status'])
        return Response(self.get_serializer(order).data)


class OrderCreateView(CheckoutHoursMixin, generics.CreateAPIView):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]


class OrderListView(generics.ListAPIView):
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Order.objects.filter(user=self.request.user).select_related('vendor', 'location').prefetch_related('items__product').order_by('-created_at')


class OrderDetailView(OrderViewSet):
    """Compatibility view for integrations using the former class name."""


class IsRiderPermission(IsAuthenticated):
    """Custom permission to check if the user is a rider."""

    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.role == 'rider'


class RiderOrderViewSet(viewsets.ModelViewSet):
    serializer_class = RiderOrderSerializer
    order_model = Order
    permission_classes = [IsRiderPermission]  # Only riders can access this

    def get_queryset(self):
        # For list action: exclude delivered/cancelled so the screen isn't cluttered
        # For update actions: include all non-cancelled orders so status transitions work
        if self.action in ['update', 'partial_update']:
            return self.order_model.objects.filter(
                rider=self.request.user
            ).exclude(status='cancelled').select_for_update(of=('self',)).order_by('created_at')
        return self.order_model.objects.filter(
            rider=self.request.user
        ).exclude(status__in=['delivered', 'cancelled']).select_related('user', 'vendor', 'location').prefetch_related('items__product').order_by('created_at')

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        """
        Allow riders to update the status of their assigned orders 
        (e.g., from 'preparing' to 'out_for_delivery' to 'delivered').
        """
        try:
            order = self.get_object()
        except Http404:
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
            if not delivery_code_matches(order, provided_code):
                return Response({'error': 'Order not found for today.'}, status=status.HTTP_400_BAD_REQUEST)

        order.status = new_status
        order.save(update_fields=['status'])

        return Response(self.get_serializer(order).data)


##################################################################################### Delivery Zone API & Views ###############################################################

class DeliveryZoneViewSet(viewsets.ModelViewSet):
    queryset = DeliveryZone.objects.all()
    serializer_class = DeliveryZoneSerializer
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [CanManageDeliveryZones]

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        payload = DeliveryZoneCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        if data['is_primary']:
            DeliveryZone.objects.filter(is_primary=True).update(is_primary=False)
        zone = DeliveryZone.objects.create(**data)
        return Response(DeliveryZoneSerializer(zone).data, status=status.HTTP_201_CREATED)


class VendorMapContextMixin(UserPassesTestMixin):
    login_url = '/dispatch/login/'

    def test_func(self):
        return is_platform_admin(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['vendors'] = list(Vendor.objects.filter(is_active=True).values('id', 'shop_name', 'lat', 'lng'))
        zone = primary_delivery_zone()
        point = zone.polygon.centroid if zone and not zone.polygon.empty else None
        context['map_center'] = {'lat': point.y, 'lng': point.x} if point else {'lat': 5.9396, 'lng': 80.5760}
        context['google_maps_api_key'] = getattr(settings, 'GOOGLE_MAPS_API_KEY', '')
        return context


@method_decorator(ensure_csrf_cookie, name='dispatch')
class DeliveryZoneAdminView(VendorMapContextMixin, TemplateView):
    template_name = 'orders/delivery_zone_map.html'



##################################################################################### Daily Assignment API & Views ###############################################################

class DispatchAPIView(APIView):
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsPlatformAdmin]


class DailySummaryAPIView(DispatchAPIView):

    def get(self, request, *args, **kwargs):
        colombo_tz = pytz.timezone('Asia/Colombo')
        now = timezone.now().astimezone(colombo_tz)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)

        summary = OrderItem.objects.filter(
            order__created_at__range=(today_start, today_end)
        ).exclude(order__status='cancelled').values(
            vendor_id=F('order__vendor_id'), vendor_name=F('order__vendor__shop_name'), product_name=F('product__name')
        ).annotate(
            total_quantity=Sum('quantity')
        ).order_by('-total_quantity')

        return Response(list(summary))


class UnassignedOrdersAPIView(DispatchAPIView):

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
                    'vendor': o.vendor_id,
                    'shop_name': o.vendor.shop_name,
                })
        return Response(result)


class BulkAssignRiderAPIView(DispatchAPIView):

    def post(self, request, *args, **kwargs):
        try:
            _, updated_count = assign_orders(request.data.get('order_ids'), request.data.get('rider_id'))
        except AssignmentError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        
        return Response({'message': f'Assigned {updated_count} orders to rider.'})


class RidersListAPIView(DispatchAPIView):

    def get(self, request, *args, **kwargs):
        riders = CustomUser.objects.filter(role='rider', is_active=True).values('id', 'name', 'phone_number')
        return Response(list(riders))


@method_decorator(ensure_csrf_cookie, name='dispatch')
class DailyAssignmentAdminView(VendorMapContextMixin, TemplateView):
    template_name = 'orders/daily_assignment.html'


class RiderOrdersWithRankAPIView(APIView):
    """
    GET /api/orders/rider/orders/with-rank/

    Returns today's active orders for the authenticated rider, each enriched with
    a `rank` field pulled from MongoDB route_states.

    - If no route has been optimized yet, every order returns `"rank": null`.
    - Orders are sorted: ranked orders first (by rank asc), then unranked.
    """
    permission_classes = [IsRiderPermission]
    order_model = Order
    serializer_class = RiderOrderSerializer
    route_collection = None
    select_related_fields = ('user', 'vendor', 'location')

    def get(self, request, *args, **kwargs):
        colombo_tz = pytz.timezone('Asia/Colombo')
        now = timezone.now().astimezone(colombo_tz)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        today_str = now.strftime('%Y-%m-%d')

        # 1. Fetch today's active orders from PostgreSQL
        orders = self.order_model.objects.filter(
            rider=request.user,
            created_at__range=(today_start, today_end)
        ).exclude(status__in=['delivered', 'cancelled']).select_related(*self.select_related_fields).prefetch_related('items__product').order_by('created_at')

        # 2. Try to load today's rank map from MongoDB
        rank_map = {}  # { order_id (int): rank (int) }
        mongo_uri = getattr(settings, 'MONGO_URI', None)
        if mongo_uri:
            try:
                from pymongo import MongoClient
                client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
                db = client[getattr(settings, 'MONGO_DATABASE', 'MealDrop')]
                collection = db[self.route_collection or getattr(settings, 'MONGO_ROUTE_COLLECTION', 'multivendor_route_states')]
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
        serializer = self.serializer_class(orders, many=True)
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
    order_model = Order
    route_collection = None

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

        orders = self.order_model.objects.filter(
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
            response = requests.get(url, params=params, timeout=15)
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
            client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
            db = client[getattr(settings, 'MONGO_DATABASE', 'MealDrop')]
            collection = db[self.route_collection or getattr(settings, 'MONGO_ROUTE_COLLECTION', 'multivendor_route_states')]

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
    route_collection = None

    def get(self, request, *args, **kwargs):
        colombo_tz = pytz.timezone('Asia/Colombo')
        now = timezone.now().astimezone(colombo_tz)
        today_str = now.strftime('%Y-%m-%d')

        mongo_uri = getattr(settings, 'MONGO_URI', None)
        if not mongo_uri:
            return Response({'error': 'MONGO_URI not configured.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        try:
            from pymongo import MongoClient
            client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
            db = client[getattr(settings, 'MONGO_DATABASE', 'MealDrop')]
            collection = db[self.route_collection or getattr(settings, 'MONGO_ROUTE_COLLECTION', 'multivendor_route_states')]

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
