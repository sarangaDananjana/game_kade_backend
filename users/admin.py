from django.contrib import admin
from django.conf import settings
from django.db.models import OuterRef, Subquery
from django.utils import timezone
from .models import CustomUser, OTP, AppVersion


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
    list_display = ('user', 'otp_code', 'code_status', 'created_at', 'expires_at', 'failed_attempts')
    list_filter = ('is_used', 'created_at')
    search_fields = ('user__phone_number', 'otp_code')
    readonly_fields = ('created_at', 'expires_at', 'failed_attempts')
    ordering = ('-created_at', '-pk')

    def get_queryset(self, request):
        latest = OTP.objects.filter(user_id=OuterRef('user_id')).order_by('-created_at', '-pk')
        return super().get_queryset(request).annotate(latest_otp_id=Subquery(latest.values('pk')[:1]))

    @admin.display(description='Status')
    def code_status(self, obj):
        if obj.failed_attempts >= settings.OTP_MAX_FAILED_ATTEMPTS:
            return 'Locked'
        if obj.is_used:
            return 'Used / replaced'
        if getattr(obj, 'latest_otp_id', obj.pk) != obj.pk:
            return 'Replaced'
        if timezone.now() > obj.expires_at:
            return 'Expired'
        return 'Active'

    @admin.display(description='Expires at', ordering='created_at')
    def expires_at(self, obj):
        return obj.expires_at

@admin.register(AppVersion)
class AppVersionAdmin(admin.ModelAdmin):
    list_display = ('version_code', 'link', 'created_at')
    search_fields = ('version_code',)
    ordering = ('-created_at',)
