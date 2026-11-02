from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from django.utils import timezone

from .models import JobOffer, RiderCancellation, RiderInfo, RiderPenalty, Session, SessionOrder
from .services import dispatch_due
from .services import RiderActionError
from .admin_services import update_session_from_admin
from .profile import profile_completion


class SessionOrderInline(admin.TabularInline):
    model = SessionOrder
    extra = 0
    readonly_fields = ['order_reference', 'delivery_fee', 'created_at']


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ['id', 'vendor', 'rider_control', 'status_control', 'rider_revenue', 'finance_control', 'date', 'save_control']
    change_list_template = 'admin/riders/session/change_list.html'
    list_filter = ['vendor', 'status', 'finance_status', 'date']
    readonly_fields = ['rider_reference', 'rider_revenue', 'total_revenue', 'session_time', 'created_at', 'updated_at']
    inlines = [SessionOrderInline]
    actions = ['dispatch_sessions', 'confirm_finances']

    def get_urls(self):
        return [path('<int:session_id>/update-row/', self.admin_site.admin_view(self.update_row), name='riders_session_update_row')] + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context)
        if hasattr(response, 'context_data') and response.context_data and 'cl' in response.context_data:
            profiles = RiderInfo.objects.select_related('user').filter(user__role='rider', user__is_active=True)
            choices = [(p.user_id, f'{p.user.name or "Rider"} · {p.user.phone_number}') for p in profiles if profile_completion(p)['is_complete']]
            for session in response.context_data['cl'].result_list:
                session._admin_rider_choices = choices
        return response

    def _select(self, obj, field, choices, value, label):
        options = format_html_join('', '<option value="{}"{}>{}</option>',
            [(key, ' selected' if str(key) == str(value or '') else '', text) for key, text in choices])
        return format_html('<select name="{}" form="session-row-{}" aria-label="{} for session {}">{}</select>', field, obj.pk, label, obj.pk, options)

    @admin.display(description='Assigned rider')
    def rider_control(self, obj):
        choices = [('', 'Unassigned')] + getattr(obj, '_admin_rider_choices', [])
        if obj.assigned_rider_id and not any(key == obj.assigned_rider_id for key, _ in choices):
            choices.append((obj.assigned_rider_id, str(obj.assigned_rider)))
        return self._select(obj, 'rider_id', choices, obj.assigned_rider_id, 'Rider')

    @admin.display(description='Session status')
    def status_control(self, obj):
        return self._select(obj, 'status', Session.Status.choices, obj.status, 'Status')

    @admin.display(description='Financial status')
    def finance_control(self, obj):
        return self._select(obj, 'finance_status', Session.FinanceStatus.choices, obj.finance_status, 'Financial status')

    @admin.display(description='Save changes')
    def save_control(self, obj):
        return format_html('<button type="submit" form="session-row-{}" aria-label="Save session {}">Save</button>', obj.pk, obj.pk)

    def update_row(self, request, session_id):
        session = self.get_object(request, str(session_id))
        if request.method != 'POST' or session is None or not self.has_change_permission(request, session):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        try:
            rider_id = int(request.POST['rider_id']) if request.POST.get('rider_id') else None
            update_session_from_admin(session_id, rider_id=rider_id, status=request.POST.get('status'), finance_status=request.POST.get('finance_status'))
            self.log_change(request, session, 'Updated rider, status, and finances from the session list.')
            self.message_user(request, f'Session {session_id} updated.', messages.SUCCESS)
        except (RiderActionError, ValidationError, ValueError) as error:
            self.message_user(request, '; '.join(error.messages) if isinstance(error, ValidationError) else str(error), messages.ERROR)
        return HttpResponseRedirect(reverse('admin:riders_session_changelist'))

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
    search_fields = ['user__name', 'user__phone_number', 'vehicle_registration', 'vehicle_name', 'vehicle_colour']
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
