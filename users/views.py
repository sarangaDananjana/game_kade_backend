from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.permissions import IsAuthenticated, AllowAny
from .models import CustomUser, OTP, AppVersion
from .utils import generate_otp_code, send_otp_sms, find_user_by_mobile
from .serializers import SendOTPInput, VerifyOTPInput, ProfileInput
from django.conf import settings
from django.db import transaction
from django.contrib.auth.hashers import make_password
from django.utils.crypto import constant_time_compare
from rest_framework_simplejwt.views import TokenRefreshView
from core.throttling import enforce_limits


class SendOTPView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    rate_limit_scope = 'otp_send_ip'
    rate_limit_global_scope = 'otp_send_global'

    def post(self, request):
        payload = SendOTPInput(data=request.data)
        payload.is_valid(raise_exception=True)
        phone_number = payload.validated_data['phone_number']
        name = payload.validated_data.get('name')
        role = payload.validated_data['role']

        # Check if the user exists
        user = find_user_by_mobile(phone_number)

        if not user and not name:
            CustomUser.objects.get_or_create(phone_number=phone_number, defaults={'role': role, 'password': make_password(None)})
            return Response({
                'is_new_user': True,
                'message': 'User created. Please provide your name to receive OTP.'
            }, status=status.HTTP_201_CREATED)
        if user and not user.is_active:
            return Response({'error': 'Unable to send a login code.'}, status=status.HTTP_400_BAD_REQUEST)
        if user and not user.name and not name:
            return Response({
                'is_new_user': True,
                'message': 'Please provide your name to receive OTP.'
            }, status=status.HTTP_200_OK)

        # Apply the resend budget only when issuing a code, so the two-step
        # name-entry flow can finish without waiting through an SMS cooldown.
        enforce_limits([('otp_send_phone', phone_number)])
        if not user:
            user, _ = CustomUser.objects.get_or_create(
                phone_number=phone_number, defaults={'name': name, 'role': role, 'password': make_password(None)},
            )
        otp_code = generate_otp_code()
        with transaction.atomic():
            user = CustomUser.objects.select_for_update().get(pk=user.pk)
            if not user.is_active:
                return Response({'error': 'Unable to send a login code.'}, status=status.HTTP_400_BAD_REQUEST)
            if name and not user.name:
                user.name = name
                user.save(update_fields=['name'])
            # A resend invalidates every previous code. The user lock also
            # serializes issuance with verification across workers.
            OTP.objects.filter(user=user, is_used=False).update(is_used=True)
            otp = OTP.objects.create(user=user, otp_code=otp_code)

        # Send SMS via the provided API
        bypass_sms = phone_number in getattr(settings, 'OTP_SMS_BYPASS_NUMBERS', set())
        if bypass_sms:
            # Bypass actual SMS sending for the test account to save credits
            sms_sent = True
        else:
            sms_sent = send_otp_sms(phone_number, otp_code)

        if sms_sent:
            return Response({
                'is_new_user': False,
                'message': 'Code available in the admin panel.' if bypass_sms else 'OTP sent successfully.',
                'delivery_method': 'admin' if bypass_sms else 'sms',
                'otp_length': len(otp_code),
            }, status=status.HTTP_200_OK)
        else:
            OTP.objects.filter(pk=otp.pk).update(is_used=True)
            return Response({'error': 'Failed to send OTP via SMS.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class VerifyOTPView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    rate_limit_scope = 'otp_verify_ip'

    def post(self, request):
        payload = VerifyOTPInput(data=request.data)
        payload.is_valid(raise_exception=True)
        phone_number = payload.validated_data['phone_number']
        otp_code = payload.validated_data['otp_code']
        enforce_limits([('otp_verify_phone', phone_number)])
        user = find_user_by_mobile(phone_number)
        if user is None:
            return Response({'error': 'Invalid or expired OTP.'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            user = CustomUser.objects.select_for_update().get(pk=user.pk)
            # Always inspect the latest code, including used/locked codes;
            # never fall back to an older unused login code.
            latest_otp = OTP.objects.select_for_update().filter(user=user).order_by('-created_at', '-pk').first()
            if not user.is_active or not latest_otp or not latest_otp.is_valid():
                return Response({'error': 'Invalid or expired OTP.'}, status=status.HTTP_400_BAD_REQUEST)
            if not constant_time_compare(latest_otp.otp_code, otp_code):
                latest_otp.failed_attempts += 1
                if latest_otp.failed_attempts >= settings.OTP_MAX_FAILED_ATTEMPTS:
                    latest_otp.is_used = True
                latest_otp.save(update_fields=['failed_attempts', 'is_used'])
                return Response({'error': 'Invalid or expired OTP.'}, status=status.HTTP_400_BAD_REQUEST)
            latest_otp.is_used = True
            latest_otp.save(update_fields=['is_used'])
            refresh = RefreshToken.for_user(user)

        return Response({
            'message': 'Login successful.',
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'role': user.role,
            'name': user.name
        }, status=status.HTTP_200_OK)


class RateLimitedTokenRefreshView(TokenRefreshView):
    rate_limit_scope = 'token_refresh_ip'


class DeleteAccountView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request):
        user = request.user
        # Deleting the user will cascade and permanently delete their OTPs, Orders, and OrderLocations
        user.delete()
        return Response({
            'message': 'Your account and all associated data have been permanently deleted.'
        }, status=status.HTTP_200_OK)


def home_view(request):
    return render(request, 'index.html')


def privacy_policy_view(request):
    return render(request, 'privacy-policy.html')


def delete_account_view(request):
    return render(request, 'account-delete.html')


class AppVersionView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        latest_version = AppVersion.objects.order_by('-version_code').first()
        if latest_version:
            return Response({
                'version_code': latest_version.version_code,
                'link': latest_version.link
            }, status=status.HTTP_200_OK)
        return Response({'error': 'No version information found.'}, status=status.HTTP_404_NOT_FOUND)

class ProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        return Response({
            'name': user.name or "",
            'phone': user.phone_number,
            'role': user.role
        }, status=status.HTTP_200_OK)

    def put(self, request):
        user = request.user
        payload = ProfileInput(data=request.data)
        payload.is_valid(raise_exception=True)
        user.name = payload.validated_data.get('name', user.name)
        user.save(update_fields=['name'])
        return Response({
            'name': user.name or "",
            'phone': user.phone_number,
            'role': user.role
        }, status=status.HTTP_200_OK)
