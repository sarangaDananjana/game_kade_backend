import uuid
from decimal import Decimal
from django import forms
from .models import RiderInfo


class CollectedTopUpForm(forms.Form):
    rider = forms.ModelChoiceField(queryset=RiderInfo.objects.none(), to_field_name='user', label='Rider')
    amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'), label='Amount collected (LKR)')
    payment_reference = forms.CharField(max_length=100, required=False, label='Collection reference (optional)')
    staff_note = forms.CharField(max_length=250, required=False, label='Note (optional)', widget=forms.Textarea(attrs={'rows': 3}))
    request_id = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['rider'].queryset = RiderInfo.objects.filter(user__is_active=True, user__role='rider').select_related('user').order_by('user__name', 'user_id')
        self.fields['rider'].label_from_instance = lambda obj: f'{obj.user.name or "Rider"} · {obj.user.phone_number}'
