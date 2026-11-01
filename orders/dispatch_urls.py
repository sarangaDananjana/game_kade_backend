from django.urls import path

from .dispatch import (
    admin_dashboard_view, admin_login_view, admin_logout_view,
    admin_orders_api, admin_assign_orders_api, admin_unassign_orders_api,
)

urlpatterns = [
    path('', admin_dashboard_view, name='dispatch-dashboard'),
    path('login/', admin_login_view, name='dispatch-login'),
    path('logout/', admin_logout_view, name='dispatch-logout'),
    path('api/orders/', admin_orders_api, name='dispatch-orders'),
    path('api/assign/', admin_assign_orders_api, name='dispatch-assign'),
    path('api/unassign/', admin_unassign_orders_api, name='dispatch-unassign'),
]
