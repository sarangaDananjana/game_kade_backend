from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from .models import CustomUser, OTP
from .utils import generate_otp_code, send_otp_sms


class SendOTPView(APIView):
    def post(self, request):
        phone_number = request.data.get('phone_number')
        name = request.data.get('name')

        if not phone_number:
            return Response({'error': 'Phone number is required.'}, status=status.HTTP_400_BAD_REQUEST)

        # Check if the user exists
        user = CustomUser.objects.filter(phone_number=phone_number).first()

        if not user:
            # NEW USER FLOW
            if not name:
                # Create the user in the database, but ask for the name before sending OTP
                CustomUser.objects.create(phone_number=phone_number)
                return Response({
                    'is_new_user': True,
                    'message': 'User created. Please provide your name to receive OTP.'
                }, status=status.HTTP_201_CREATED)
            else:
                # If they somehow passed the name on the first try, create them completely
                user = CustomUser.objects.create(
                    phone_number=phone_number, name=name)
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

        # Generate OTP
        otp_code = generate_otp_code()

        # Save OTP to database
        OTP.objects.create(user=user, otp_code=otp_code)

        # Send SMS via the provided API
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
        }, status=status.HTTP_200_OK)
