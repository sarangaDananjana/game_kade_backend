from rest_framework import serializers

from .utils import normalize_mobile_number


class PhoneInput(serializers.Serializer):
    phone_number = serializers.CharField(max_length=32)

    def validate_phone_number(self, value):
        try:
            return normalize_mobile_number(value)
        except ValueError as error:
            raise serializers.ValidationError(str(error)) from None


class SendOTPInput(PhoneInput):
    name = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True)
    role = serializers.ChoiceField(choices=['customer', 'rider'], default='customer')


class VerifyOTPInput(PhoneInput):
    otp_code = serializers.RegexField(
        r'^[0-9]{6}$', min_length=6, max_length=6,
        error_messages={
            'invalid': 'Enter the six-digit OTP.',
            'min_length': 'Enter the six-digit OTP.',
            'max_length': 'Enter the six-digit OTP.',
        },
    )


class ProfileInput(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True)
