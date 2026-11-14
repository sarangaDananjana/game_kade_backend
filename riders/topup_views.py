from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from vendors.permissions import is_platform_admin
from .models import RiderInfo, RiderTopUp
from .services import RiderActionError
from .topup_forms import CollectedTopUpForm
from .wallet import review_topup


@ensure_csrf_cookie
@require_http_methods(['GET', 'POST'])
def rider_topup_view(request):
    if not is_platform_admin(request.user):
        return redirect(f'{reverse("dispatch-login")}?next={request.path}')
    form = CollectedTopUpForm(request.POST or None, initial={'rider': request.GET.get('rider')})
    if request.method == 'POST' and form.is_valid():
        values = form.cleaned_data
        try:
            with transaction.atomic():
                profile = RiderInfo.objects.select_for_update().get(pk=values['rider'].pk, user__is_active=True, user__role='rider')
                topup = RiderTopUp.objects.filter(request_id=values['request_id']).first()
                repeated = topup is not None
                reference = values['payment_reference'] or str(values['request_id'])
                if topup:
                    if (topup.rider_id, topup.amount, topup.payment_reference) != (profile.pk, values['amount'], reference):
                        raise RiderActionError('This submission reference was already used. Reload the form before adding another payment.')
                else:
                    topup = RiderTopUp.objects.create(rider=profile, request_id=values['request_id'], amount=values['amount'], payment_reference=reference)
                review_topup(topup.pk, request.user, True, values['staff_note'])
            messages.success(request, 'This top-up was already recorded; no second credit was added.' if repeated else f'Rs. {values["amount"]:.2f} credited to {profile.user.name or profile.user.phone_number}.')
            return redirect(f'{reverse("dispatch-rider-topup")}?rider={profile.user_id}')
        except (RiderActionError, ValidationError) as error:
            form.add_error(None, '; '.join(error.messages) if isinstance(error, ValidationError) else str(error))
        except IntegrityError:
            form.add_error(None, 'That collection reference has already been recorded. Check the existing payment before trying again.')
        except RiderInfo.DoesNotExist:
            form.add_error('rider', 'Choose an active rider.')
    riders = list(form.fields['rider'].queryset)
    return render(request, 'riders/topup.html', {'form': form,
        'rider_balances': {str(r.user_id): str(r.topup_balance) for r in riders},
        'timezone_label': 'Sri Lanka time (UTC+05:30)'})
