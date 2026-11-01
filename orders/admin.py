from django.contrib.gis import admin
from django.urls import path
from django.core.serializers import serialize
from django.template.response import TemplateResponse
from .models import OrderLocation, Order, OrderItem, DeliveryZone, DeliveryDistanceTier


@admin.register(OrderLocation)
class OrderLocationAdmin(admin.ModelAdmin):
    list_display = ('name', 'user', 'district', 'unique_identity', 'is_system_defined', 'h3_index')
    list_filter = ('is_system_defined', 'district')
    search_fields = ('name', 'description', 'unique_identity',
                     'user__phone_number', 'user__name')

# This allows you to see and edit the Order Items directly inside the Order page


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('price_at_purchase',)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'vendor', 'user', 'rider', 'status', 'total_amount', 'delivery_fee', 'created_at')
    list_filter = ('vendor', 'status', 'created_at')
    search_fields = ('user__phone_number', 'user__name', 'vendor__shop_name', 'delivery_code')
    readonly_fields = ('delivery_code', 'legacy_delivery_code', 'created_at')
    inlines = [OrderItemInline]
    # Quickly change order status from the list view
    list_editable = ('status',)


@admin.register(DeliveryZone)
class DeliveryZoneAdmin(admin.GISModelAdmin):
    list_display = ('name', 'district', 'is_active', 'is_primary')
    list_filter = ('district', 'is_active', 'is_primary')
    search_fields = ('name', 'district')

    gis_widget_kwargs = {
        'attrs': {
            'default_lon': 80.5760,
            'default_lat': 5.9396,
            'default_zoom': 13,
        }
    }

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('combined-map/', self.admin_site.admin_view(self.combined_map_view), name='orders_deliveryzone_combined_map'),
        ]
        return custom_urls + urls

    def combined_map_view(self, request):
        zones = DeliveryZone.objects.filter(is_active=True)
        geojson = serialize('geojson', zones, geometry_field='polygon', fields=('name',))
        
        context = dict(
            self.admin_site.each_context(request),
            geojson=geojson,
            title="Combined Delivery Zones",
        )
        return TemplateResponse(request, "admin/deliveryzone_combined_map.html", context)

    def change_view(self, request, object_id, form_url='', extra_context=None):
        extra_context = extra_context or {}
        zones = DeliveryZone.objects.filter(is_active=True).exclude(pk=object_id)
        extra_context['existing_zones_geojson'] = serialize('geojson', zones, geometry_field='polygon', fields=('name',))
        return super().change_view(request, object_id, form_url, extra_context=extra_context)

    def add_view(self, request, form_url='', extra_context=None):
        extra_context = extra_context or {}
        zones = DeliveryZone.objects.filter(is_active=True)
        extra_context['existing_zones_geojson'] = serialize('geojson', zones, geometry_field='polygon', fields=('name',))
        return super().add_view(request, form_url, extra_context=extra_context)


@admin.register(DeliveryDistanceTier)
class DeliveryDistanceTierAdmin(admin.ModelAdmin):
    list_display = ('grid_distance', 'delivery_fee', 'minimum_delivery_charge_per_parcel')
    list_editable = ('delivery_fee', 'minimum_delivery_charge_per_parcel')
    ordering = ('grid_distance',)
