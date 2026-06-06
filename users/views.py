from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.permissions import IsAuthenticated, AllowAny
from .models import CustomUser, OTP, AppVersion
from .utils import generate_otp_code, send_otp_sms


class SendOTPView(APIView):
    def post(self, request):
        phone_number = request.data.get('phone_number')
        name = request.data.get('name')
        # NEW: Extract the role from the request, default to 'customer' if missing
        role = request.data.get('role', 'customer')

        # Security check: Ensure they don't try to pass 'admin' or something invalid
        if role not in ['customer', 'rider']:
            role = 'customer'

        if not phone_number:
            return Response({'error': 'Phone number is required.'}, status=status.HTTP_400_BAD_REQUEST)

        # Check if the user exists
        user = CustomUser.objects.filter(phone_number=phone_number).first()

        if not user:
            # NEW USER FLOW
            if not name:
                # Create the user in the database, assigning the requested role
                CustomUser.objects.create(phone_number=phone_number, role=role)
                return Response({
                    'is_new_user': True,
                    'message': 'User created. Please provide your name to receive OTP.'
                }, status=status.HTTP_201_CREATED)
            else:
                # If they somehow passed the name on the first try, create them completely
                user = CustomUser.objects.create(
                    phone_number=phone_number, name=name, role=role)
        else:
            # EXISTING USER FLOW
            if not user.name and not name:
                # Edge case: User was created but dropped off before entering name
                return Response({
                    'is_new_user': True,
                    'message': 'Please provide your name to receive OTP.'
                }, status=status.HTTP_200_OK)
            elif name and not user.name:
                # Update user with the provided name
                user.name = name
                user.save()

        # Generate OTP (Modified for test number bypass)
        if phone_number == "0700000000":
            otp_code = "00000"
        else:
            otp_code = generate_otp_code()

        # Save OTP to database
        OTP.objects.create(user=user, otp_code=otp_code)

        # Send SMS via the provided API
        if phone_number == "0700000000":
            # Bypass actual SMS sending for the test account to save credits
            sms_sent = True
        else:
            sms_sent = send_otp_sms(phone_number, otp_code)

        if sms_sent:
            return Response({
                'is_new_user': False,
                'message': 'OTP sent successfully.'
            }, status=status.HTTP_200_OK)
        else:
            return Response({'error': 'Failed to send OTP via SMS.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class VerifyOTPView(APIView):
    def post(self, request):
        phone_number = request.data.get('phone_number')
        otp_code = request.data.get('otp_code')

        if not phone_number or not otp_code:
            return Response({'error': 'Phone number and OTP code are required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = CustomUser.objects.get(phone_number=phone_number)
        except CustomUser.DoesNotExist:
            return Response({'error': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Find the latest unused OTP for this user
        latest_otp = OTP.objects.filter(
            user=user, is_used=False).order_by('-created_at').first()

        if not latest_otp or latest_otp.otp_code != str(otp_code):
            return Response({'error': 'Invalid OTP.'}, status=status.HTTP_400_BAD_REQUEST)

        if not latest_otp.is_valid():
            return Response({'error': 'OTP has expired.'}, status=status.HTTP_400_BAD_REQUEST)

        # Mark OTP as used
        latest_otp.is_used = True
        latest_otp.save()

        # Ensure user is active
        if not user.is_active:
            user.is_active = True
            user.save()

        # Generate JWT Tokens
        refresh = RefreshToken.for_user(user)

        return Response({
            'message': 'Login successful.',
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'role': user.role,
            'name': user.name
        }, status=status.HTTP_200_OK)


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
