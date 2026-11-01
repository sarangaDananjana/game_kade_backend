"""Compatibility imports; serializers live with their shared domain models."""

from orders.serializers import (
    OrderItemSerializer, OrderLocationSerializer, OrderSerializer,
    RiderOrderLocationSerializer, RiderOrderSerializer,
)
from products.serializers import ProductSerializer
from vendors.serializers import VendorSerializer

__all__ = ['OrderItemSerializer', 'OrderLocationSerializer', 'OrderSerializer',
           'RiderOrderLocationSerializer', 'RiderOrderSerializer', 'ProductSerializer', 'VendorSerializer']
