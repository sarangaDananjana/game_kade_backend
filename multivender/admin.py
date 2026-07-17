from django.contrib import admin
from .models import DeliveryZone, Vendor, Product, OrderLocation, Order, OrderItem, DeliveryDistanceTier

class ProductInline(admin.TabularInline):
    model = Product
    extra = 1

@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ('shop_name', 'owner', 'is_pickup_only', 'is_active', 'created_at', 'h3_index')
    list_filter = ('is_active', 'is_pickup_only')
    search_fields = ('shop_name', 'owner__phone_number', 'h3_index')
    inlines = [ProductInline]

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'vendor', 'user', 'status', 'total_amount', 'delivery_fee', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('id', 'user__phone_number', 'vendor__shop_name', 'delivery_code')
    inlines = [OrderItemInline]

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'vendor', 'category', 'price', 'stock_quantity', 'is_available')
    list_filter = ('category', 'is_available')
    search_fields = ('name', 'vendor__shop_name')

@admin.register(OrderLocation)
class OrderLocationAdmin(admin.ModelAdmin):
    list_display = ('name', 'user', 'unique_identity', 'is_system_defined', 'h3_index')
    list_filter = ('is_system_defined',)
    search_fields = ('name', 'unique_identity', 'user__phone_number', 'h3_index')

@admin.register(DeliveryDistanceTier)
class DeliveryDistanceTierAdmin(admin.ModelAdmin):
    list_display = ('grid_distance', 'delivery_fee')
    search_fields = ('grid_distance',)

admin.site.register(DeliveryZone)
