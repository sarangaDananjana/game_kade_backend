"""Compatibility exports for existing /shop/ integrations."""

from vendors.views import VendorListView, VendorDetailView, multivendor_web_view
from orders.views import OrderCreateView, OrderListView, OrderDetailView, OrderLocationViewSet
from orders.dispatch import (
    is_admin_user, admin_dashboard_view, admin_login_view, admin_logout_view,
    admin_orders_api, admin_assign_orders_api, admin_unassign_orders_api,
)

__all__ = ['VendorListView', 'VendorDetailView', 'multivendor_web_view',
           'OrderCreateView', 'OrderListView', 'OrderDetailView', 'OrderLocationViewSet',
           'is_admin_user', 'admin_dashboard_view', 'admin_login_view', 'admin_logout_view',
           'admin_orders_api', 'admin_assign_orders_api', 'admin_unassign_orders_api']
