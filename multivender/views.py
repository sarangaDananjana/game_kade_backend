from django.shortcuts import render
from rest_framework import generics, permissions
from .models import Vendor, Order, OrderLocation
from .serializers import VendorSerializer, OrderSerializer, OrderLocationSerializer

class VendorListView(generics.ListAPIView):
    queryset = Vendor.objects.filter(is_active=True)
    serializer_class = VendorSerializer
    permission_classes = [permissions.AllowAny]

class VendorDetailView(generics.RetrieveAPIView):
    queryset = Vendor.objects.filter(is_active=True)
    serializer_class = VendorSerializer
    permission_classes = [permissions.AllowAny]

class OrderLocationListView(generics.ListAPIView):
    queryset = OrderLocation.objects.filter(is_system_defined=True)
    serializer_class = OrderLocationSerializer
    permission_classes = [permissions.AllowAny]

class OrderCreateView(generics.CreateAPIView):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

class OrderListView(generics.ListAPIView):
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Order.objects.filter(user=self.request.user).order_by('-created_at')

def multivendor_web_view(request):
    return render(request, 'multivendor_app.html')
