import json
import pytz
from datetime import timedelta
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import ensure_csrf_cookie
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import generics, permissions, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticatedOrReadOnly

from users.models import CustomUser
from .models import Vendor, Order, OrderLocation
from .serializers import VendorSerializer, OrderSerializer, OrderLocationSerializer

# ==============================================================================
# EXISTING API VIEWS
# ==============================================================================

class VendorListView(generics.ListAPIView):
    queryset = Vendor.objects.filter(is_active=True)
    serializer_class = VendorSerializer
    permission_classes = [permissions.AllowAny]

    def get_serializer_context(self):
        context = super().get_serializer_context()
        order_location_id = self.request.query_params.get('order_location_id')
        if order_location_id:
            try:
                context['order_location'] = OrderLocation.objects.get(id=order_location_id)
            except OrderLocation.DoesNotExist:
                pass
        return context

class VendorDetailView(generics.RetrieveAPIView):
    queryset = Vendor.objects.filter(is_active=True)
    serializer_class = VendorSerializer
    permission_classes = [permissions.AllowAny]

    def get_serializer_context(self):
        context = super().get_serializer_context()
        order_location_id = self.request.query_params.get('order_location_id')
        if order_location_id:
            try:
                context['order_location'] = OrderLocation.objects.get(id=order_location_id)
            except OrderLocation.DoesNotExist:
                pass
        return context

class OrderLocationViewSet(viewsets.ModelViewSet):
    serializer_class = OrderLocationSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        user = self.request.user
        if user.is_authenticated:
            return OrderLocation.objects.filter(
                Q(is_system_defined=True) | Q(user=user)
            )
        return OrderLocation.objects.filter(is_system_defined=True)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, is_system_defined=False)

class OrderCreateView(generics.CreateAPIView):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        now_utc = timezone.now()
        sl_time = now_utc + timedelta(hours=5, minutes=30)
        
        if not (12 <= sl_time.hour < 17):
            raise ValidationError("We are currently closed. Orders are only accepted between 12:00 PM and 5:00 PM.")
            
        serializer.save(user=self.request.user)

class OrderListView(generics.ListAPIView):
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Order.objects.filter(user=self.request.user).order_by('-created_at')

class OrderDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Order.objects.filter(user=self.request.user)

    def perform_update(self, serializer):
        instance = self.get_object()
        new_status = self.request.data.get('status')
        if new_status == 'cancelled':
            if instance.status != 'pending':
                raise ValidationError("Cannot cancel order that is already being prepared or out for delivery.")
            serializer.save(status='cancelled')
        else:
            raise ValidationError("You can only cancel pending orders.")

def multivendor_web_view(request):
    return render(request, 'multivendor_app.html')


# ==============================================================================
# MULTI-VENDOR ADMIN DASHBOARD & ORDER ASSIGNMENT
# ==============================================================================

def is_admin_user(user):
    """Checks if the user has admin/staff privileges."""
    return user.is_authenticated and (
        user.is_staff or 
        user.is_superuser or 
        getattr(user, 'role', '') == 'admin'
    )

def admin_login_view(request):
    """
    Renders login view for admins only.
    Authenticates by phone number (CustomUser.USERNAME_FIELD) and password.
    """
    if is_admin_user(request.user):
        return redirect('multivendor-admin-dashboard')
        
    error = None
    if request.method == 'POST':
        phone_number = request.POST.get('phone_number', '').strip()
        password = request.POST.get('password', '').strip()
        
        user = authenticate(request, phone_number=phone_number, password=password)
        if not user:
            user = authenticate(request, username=phone_number, password=password)
            
        if user and is_admin_user(user):
            login(request, user)
            next_url = request.GET.get('next') or request.POST.get('next') or '/shop/admin/dashboard/'
            return redirect(next_url)
        else:
            existing = CustomUser.objects.filter(phone_number=phone_number).first()
            if existing and not is_admin_user(existing):
                error = "Access denied: Your account role does not have administrator privileges."
            else:
                error = "Invalid phone number or password. Please try again."
                
    return render(request, 'multivendor_admin_login.html', {
        'error': error,
        'next': request.GET.get('next', '')
    })

def admin_logout_view(request):
    """Logs out the current admin user."""
    logout(request)
    return redirect('multivendor-admin-login')

@ensure_csrf_cookie
def admin_dashboard_view(request):
    """Renders the custom Multi-Vendor Admin Dashboard."""
    if not is_admin_user(request.user):
        return redirect(f"/shop/admin/login/?next={request.path}")
    return render(request, 'multivendor_admin_dashboard.html', {
        'admin_user': request.user
    })

def admin_orders_api(request):
    """
    GET API: Cumulates all today's multi-vendor orders and groups them into:
      1. System Defined Locations (is_system_defined == True)
      2. Custom Locations (is_system_defined == False / custom user addresses)
    Also returns active registered riders and statistical summaries.
    """
    if not is_admin_user(request.user):
        return JsonResponse({'error': 'Unauthorized. Admin credentials required.'}, status=403)
        
    colombo_tz = pytz.timezone('Asia/Colombo')
    now = timezone.now().astimezone(colombo_tz)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    
    # Query today's orders
    orders_qs = Order.objects.filter(
        created_at__range=(today_start, today_end)
    ).select_related('user', 'vendor', 'location', 'rider').prefetch_related('items__product').order_by('-created_at')
    
    # Active registered riders
    riders_qs = CustomUser.objects.filter(role='rider', is_active=True).order_by('name')
    
    # Count assigned orders for each rider today (excluding cancelled)
    assigned_counts = Order.objects.filter(
        created_at__range=(today_start, today_end),
        rider__isnull=False
    ).exclude(status='cancelled').values('rider_id').annotate(total=Count('id'))
    counts_map = {item['rider_id']: item['total'] for item in assigned_counts}
    
    riders_data = [
        {
            'id': r.id,
            'name': r.name or f"Rider #{r.id}",
            'phone_number': r.phone_number,
            'assigned_today_count': counts_map.get(r.id, 0)
        }
        for r in riders_qs
    ]
    
    def serialize_order(o):
        return {
            'id': o.id,
            'status': o.status,
            'status_display': o.get_status_display(),
            'total_amount': str(o.total_amount),
            'delivery_fee': str(o.delivery_fee),
            'delivery_code': str(o.delivery_code),
            'created_at': o.created_at.astimezone(colombo_tz).strftime('%I:%M %p'),
            'created_date': o.created_at.astimezone(colombo_tz).strftime('%Y-%m-%d'),
            'customer': {
                'id': o.user.id if o.user else None,
                'name': o.user.name or 'Customer' if o.user else 'Guest',
                'phone': o.user.phone_number if o.user else '',
            },
            'vendor': {
                'id': o.vendor.id,
                'name': o.vendor.shop_name,
                'district': o.vendor.district or '',
                'lat': o.vendor.lat,
                'lng': o.vendor.lng,
            } if o.vendor else {
                'id': None,
                'name': 'Unknown Vendor',
                'district': '',
                'lat': None,
                'lng': None,
            },
            'location': {
                'id': o.location.id if o.location else None,
                'name': o.location.name if o.location else 'Custom Location',
                'description': o.location.description if o.location else '',
                'landmark': o.location.unique_identity if o.location else '',
                'district': o.location.district if o.location else '',
                'is_system_defined': o.location.is_system_defined if o.location else False,
                'lat': o.location.lat if o.location else None,
                'lng': o.location.lng if o.location else None,
            } if o.location else {
                'id': None,
                'name': 'Custom Delivery',
                'description': 'Direct customer drop-off',
                'landmark': '',
                'district': '',
                'is_system_defined': False,
                'lat': None,
                'lng': None,
            },
            'rider': {
                'id': o.rider.id,
                'name': o.rider.name or f"Rider #{o.rider.id}",
                'phone': o.rider.phone_number,
            } if o.rider else None,
            'items': [
                {
                    'product_id': item.product.id if item.product else None,
                    'product_name': item.product.name if item.product else 'Custom Item',
                    'quantity': item.quantity,
                    'price': str(item.price_at_purchase),
                }
                for item in o.items.all()
            ]
        }
    
    system_location_orders = []
    custom_location_orders = []
    
    for o in orders_qs:
        serialized = serialize_order(o)
        if o.location and o.location.is_system_defined:
            system_location_orders.append(serialized)
        else:
            custom_location_orders.append(serialized)
            
    stats = {
        'total_orders': len(orders_qs),
        'system_orders_count': len(system_location_orders),
        'custom_orders_count': len(custom_location_orders),
        'unassigned_count': sum(1 for o in orders_qs if not o.rider and o.status != 'cancelled'),
        'assigned_count': sum(1 for o in orders_qs if o.rider and o.status != 'cancelled'),
        'total_riders': len(riders_data),
        'today_str': now.strftime('%A, %d %B %Y')
    }
    
    return JsonResponse({
        'stats': stats,
        'riders': riders_data,
        'system_location_orders': system_location_orders,
        'custom_location_orders': custom_location_orders,
    })

@require_POST
def admin_assign_orders_api(request):
    """
    POST API: Manually assigns a list of order IDs to a selected registered rider.
    Expects JSON: { "rider_id": 3, "order_ids": [12, 13, 14] }
    """
    if not is_admin_user(request.user):
        return JsonResponse({'error': 'Unauthorized. Admin credentials required.'}, status=403)
        
    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'error': 'Invalid JSON request payload.'}, status=400)
        
    rider_id = data.get('rider_id')
    order_ids = data.get('order_ids', [])
    
    if not rider_id:
        return JsonResponse({'error': 'Please select a rider from the available riders list.'}, status=400)
    if not order_ids or not isinstance(order_ids, list):
        return JsonResponse({'error': 'Please select at least one order to assign.'}, status=400)
        
    rider = CustomUser.objects.filter(id=rider_id, role='rider', is_active=True).first()
    if not rider:
        return JsonResponse({'error': 'Selected rider was not found or is currently inactive.'}, status=404)
        
    # Bulk update the rider in multivender.Order
    updated_count = Order.objects.filter(id__in=order_ids).update(rider=rider)
    
    rider_display = rider.name or rider.phone_number
    return JsonResponse({
        'success': True,
        'message': f"Assigned {updated_count} order(s) to rider {rider_display}.",
        'updated_count': updated_count,
        'rider': {
            'id': rider.id,
            'name': rider_display,
            'phone': rider.phone_number
        }
    })

@require_POST
def admin_unassign_orders_api(request):
    """
    POST API: Unassigns rider from the specified orders.
    Expects JSON: { "order_ids": [12, 13] }
    """
    if not is_admin_user(request.user):
        return JsonResponse({'error': 'Unauthorized. Admin credentials required.'}, status=403)
        
    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'error': 'Invalid JSON request payload.'}, status=400)
        
    order_ids = data.get('order_ids', [])
    if not order_ids or not isinstance(order_ids, list):
        return JsonResponse({'error': 'Please select at least one order to unassign.'}, status=400)
        
    updated_count = Order.objects.filter(id__in=order_ids).update(rider=None)
    return JsonResponse({
        'success': True,
        'message': f"Unassigned {updated_count} order(s).",
        'updated_count': updated_count
    })
