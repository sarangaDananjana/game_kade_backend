from django.contrib.gis import admin
from .models import OrderLocation, Order, OrderItem, DeliveryZone


@admin.register(OrderLocation)
class OrderLocationAdmin(admin.ModelAdmin):
    list_display = ('name', 'user', 'unique_identity', 'is_system_defined')
    list_filter = ('is_system_defined',)
    search_fields = ('name', 'description', 'unique_identity',
                     'user__phone_number', 'user__name')

# This allows you to see and edit the Order Items directly inside the Order page


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('price_at_purchase',)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'status', 'total_amount', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('user__phone_number', 'user__name', 'delivery_code')
    readonly_fields = ('delivery_code', 'created_at')
    inlines = [OrderItemInline]
    # Quickly change order status from the list view
    list_editable = ('status',)


@admin.register(DeliveryZone)
class DeliveryZoneAdmin(admin.GISModelAdmin):
    list_display = ('name', 'is_active')
    search_fields = ('name',)
