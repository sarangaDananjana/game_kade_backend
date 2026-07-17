from django.shortcuts import render
from rest_framework import generics, permissions, viewsets
from .models import Vendor, Order, OrderLocation
from .serializers import VendorSerializer, OrderSerializer, OrderLocationSerializer

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

from django.db.models import Q
from rest_framework.permissions import IsAuthenticatedOrReadOnly

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

from django.utils import timezone
from datetime import timedelta
from rest_framework.exceptions import ValidationError

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

def multivendor_web_view(request):
    return render(request, 'multivendor_app.html')
