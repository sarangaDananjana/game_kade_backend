from django.db.models import Q
from django.shortcuts import render
from rest_framework import generics, permissions
from rest_framework.exceptions import ValidationError

from orders.models import OrderLocation
from .models import Vendor
from .serializers import VendorSerializer


class VendorLocationContextMixin:
    def get_serializer_context(self):
        context = super().get_serializer_context()
        location_id = self.request.query_params.get('order_location_id')
        if location_id:
            try:
                location_id = int(location_id)
            except (TypeError, ValueError):
                raise ValidationError({'order_location_id': 'Choose a valid saved address.'})
            locations = OrderLocation.objects.filter(is_system_defined=True)
            if self.request.user.is_authenticated:
                locations = OrderLocation.objects.filter(Q(is_system_defined=True) | Q(user=self.request.user))
            context['order_location'] = locations.filter(pk=location_id).first()
            if context['order_location'] is None:
                raise ValidationError({'order_location_id': 'Choose one of your own saved addresses.'})
        return context


class VendorListView(VendorLocationContextMixin, generics.ListAPIView):
    queryset = Vendor.objects.filter(is_active=True).prefetch_related('products')
    serializer_class = VendorSerializer
    permission_classes = [permissions.AllowAny]


class VendorDetailView(VendorLocationContextMixin, generics.RetrieveAPIView):
    queryset = Vendor.objects.filter(is_active=True).prefetch_related('products')
    serializer_class = VendorSerializer
    permission_classes = [permissions.AllowAny]


def multivendor_web_view(request):
    return render(request, 'multivendor_app.html')
