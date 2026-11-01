"""Compatibility imports for integrations using the former app location.

All models are registered once, in their owning domain apps. Migration history
stays here so existing installations can upgrade without resetting a database.
"""

from orders.models import DeliveryDistanceTier, DeliveryZone, Order, OrderItem, OrderLocation
from products.models import Product
from vendors.models import DISTRICT_CHOICES, Vendor

__all__ = ['DeliveryDistanceTier', 'DeliveryZone', 'Order', 'OrderItem', 'OrderLocation', 'Product', 'Vendor', 'DISTRICT_CHOICES']
