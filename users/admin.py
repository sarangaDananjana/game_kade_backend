from django.contrib import admin
from .models import CustomUser, OTP


@admin.register(CustomUser)
class CustomUserAdmin(admin.ModelAdmin):
    # Added 'role' to list_display so you can see it at a glance
    list_display = ('phone_number', 'name', 'role',
                    'is_active', 'is_staff', 'date_joined')
    # Added 'role' to list_filter so you can easily view only "riders"
    list_filter = ('role', 'is_active', 'is_staff', 'date_joined')
    search_fields = ('phone_number', 'name')
    ordering = ('-date_joined',)


@admin.register(OTP)
class OTPAdmin(admin.ModelAdmin):
    list_display = ('user', 'otp_code', 'is_used', 'created_at', 'is_valid')
    list_filter = ('is_used', 'created_at')
    search_fields = ('user__phone_number', 'otp_code')
    readonly_fields = ('created_at',)
