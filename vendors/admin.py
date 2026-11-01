from django.contrib import admin

from products.models import Product
from .models import Vendor


class ProductInline(admin.TabularInline):
    model = Product
    extra = 0


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ('shop_name', 'owner', 'district', 'is_pickup_only', 'is_active', 'created_at', 'h3_index')
    list_filter = ('district', 'is_active', 'is_pickup_only')
    search_fields = ('shop_name', 'owner__phone_number', 'h3_index')
    inlines = [ProductInline]
