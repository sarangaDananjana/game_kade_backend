"""
URL configuration for food_delivery project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from users import views
from vendors.views import multivendor_web_view
from core.admin_redirects import legacy_model_admin
from django.urls import re_path

admin.site.site_header = "Meal Drop Administration"
admin.site.site_title = "Meal Drop Admin"
admin.site.index_title = "Meal Drop Backend"

urlpatterns = [
    re_path(r'^admin/multivender/(?P<model>vendor|product|order|orderlocation|deliverydistancetier|deliveryzone)/(?P<remaining_path>.*)$', legacy_model_admin),
    path('admin/', admin.site.urls),
    # Include users app URLs under the /api/auth/ prefix
    path('api/auth/', include('users.urls')),
    path('', views.home_view, name="home"),
    path('privacy-policy/', views.privacy_policy_view, name="privacy-policy"),
    path('delete-account/', views.delete_account_view, name='delete_account'),
    path('api/products/', include('products.urls')),
    path('api/vendors/', include('vendors.urls')),
    path('api/orders/', include('orders.urls')),
    path('dispatch/', include('orders.dispatch_urls')),
    path('menu/', multivendor_web_view, name='marketplace'),
    path('shop/', include('multivender.urls')),
]

# Serve media files (like product images) through Django directly
from django.views.static import serve

urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
]
