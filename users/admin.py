from django.contrib import admin
from .models import CustomUser, OTP


@admin.register(CustomUser)
class CustomUserAdmin(admin.ModelAdmin):
    list_display = ('phone_number', 'name', 'is_active',
                    'is_staff', 'date_joined')
    list_filter = ('is_active', 'is_staff', 'date_joined')
    search_fields = ('phone_number', 'name')
    ordering = ('-date_joined',)


@admin.register(OTP)
class OTPAdmin(admin.ModelAdmin):
    list_display = ('user', 'otp_code', 'is_used', 'created_at', 'is_valid')
    list_filter = ('is_used', 'created_at')
    search_fields = ('user__phone_number', 'otp_code')
    readonly_fields = ('created_at',)
