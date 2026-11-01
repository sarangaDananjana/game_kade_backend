from django.contrib import admin
from .models import Product, Featured


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    # 'category' added to display columns and the right-side filter sidebar
    list_display = ('name', 'vendor', 'category', 'price',
                    'stock_quantity', 'is_available', 'created_at')
    list_filter = ('vendor', 'category', 'is_available', 'created_at')
    search_fields = ('name', 'description', 'vendor__shop_name')
    # Allows quick editing from the list view
    list_editable = ('price', 'stock_quantity', 'is_available')


@admin.register(Featured)
class FeaturedAdmin(admin.ModelAdmin):
    list_display = ('topic', 'is_active', 'created_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('topic', 'description')
    list_editable = ('is_active',)
