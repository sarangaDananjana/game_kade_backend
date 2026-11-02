from django.contrib import admin, messages
from django.utils import timezone

from .models import JobOffer, RiderCancellation, RiderInfo, RiderPenalty, Session, SessionOrder
from .services import dispatch_due


class SessionOrderInline(admin.TabularInline):
    model = SessionOrder
    extra = 0
    readonly_fields = ['order_reference', 'delivery_fee', 'created_at']


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ['id', 'vendor', 'assigned_rider', 'status', 'rider_revenue', 'finance_status', 'date', 'dispatch_enabled']
    list_filter = ['vendor', 'status', 'finance_status', 'date']
    readonly_fields = ['rider_reference', 'rider_revenue', 'total_revenue', 'session_time', 'created_at', 'updated_at']
    inlines = [SessionOrderInline]
    actions = ['dispatch_sessions', 'confirm_finances']

    @admin.action(description='Offer selected planned sessions to available riders')
    def dispatch_sessions(self, request, queryset):
        count = queryset.filter(status='planned', assigned_rider=None, vendor__isnull=False).update(dispatch_enabled=True)
        dispatch_due()
        self.message_user(request, f'Queued {count} sessions for rider offers.', messages.SUCCESS)

    @admin.action(description='Confirm entered finances for completed sessions')
    def confirm_finances(self, request, queryset):
        count = 0
        for session in queryset.filter(status='completed', finance_status='draft'):
            session.finance_status, session.settled_at = Session.FinanceStatus.CONFIRMED, timezone.now()
            session.save()
            count += 1
        self.message_user(request, f'Confirmed {count} completed session revenue splits.', messages.SUCCESS)


@admin.register(RiderInfo)
class RiderInfoAdmin(admin.ModelAdmin):
    list_display = ['user', 'is_available', 'score', 'job_acceptance_window', 'cancelled_sessions_count', 'penalty_balance']
    list_filter = ['is_available', 'vehicle_type']
    search_fields = ['user__name', 'user__phone_number', 'vehicle_registration']
    readonly_fields = ['cancelled_sessions_count', 'last_cancelled_at', 'total_penalties_count', 'last_penalty_at',
                       'penalty_balance', 'penalty_points', 'created_at', 'updated_at']


@admin.register(JobOffer)
class JobOfferAdmin(admin.ModelAdmin):
    list_display = ['id', 'session', 'rider', 'status', 'acceptance_window', 'expires_at']
    list_filter = ['status']
    readonly_fields = ['session', 'rider', 'rider_reference', 'status', 'acceptance_window', 'offered_at', 'expires_at', 'responded_at', 'response_reason']
    def has_add_permission(self, request):
        return False


@admin.register(RiderCancellation)
class CancellationAdmin(admin.ModelAdmin):
    list_display = ['session_reference', 'rider_reference', 'reason', 'stage', 'occurred_at', 'released_orders_count']
    readonly_fields = [field.name for field in RiderCancellation._meta.fields]
    def has_add_permission(self, request):
        return False


@admin.register(RiderPenalty)
class PenaltyAdmin(admin.ModelAdmin):
    list_display = ['reference', 'rider_reference', 'reason', 'amount', 'points', 'status', 'issued_at']
    readonly_fields = [field.name for field in RiderPenalty._meta.fields]
    def has_add_permission(self, request):
        return False
