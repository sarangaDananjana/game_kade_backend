import requests
import random
import os  # <-- Add this


def generate_otp_code():
    """Generates a 6-digit random OTP"""
    return str(random.randint(100000, 999999))


def send_otp_sms(phone, otp_code):
    """Sends OTP using the provided text.lk API"""
    message = f"Your Food Delivery App OTP code is: {otp_code}. It will expire in 5 minutes."
    api_key = os.environ.get("TEXT_LK_API_KEY")

    try:
        response = requests.post(
            "https://app.text.lk/api/v3/sms/send",
            headers={
                # Note: In production, move this Bearer token to .env
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            json={
                "recipient": phone,
                "sender_id": "TextLKDemo",
                "type": "plain",
                "message": message
            },
            timeout=5
        )
        return response.status_code == 200
    except requests.exceptions.RequestException as e:
        print(f"Error sending SMS: {e}")
        return False
