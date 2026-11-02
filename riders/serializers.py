from math import ceil

from rest_framework import serializers

from .models import JobOffer, RiderCancellation, RiderInfo, RiderPenalty, Session
from .routes import distance_km, session_stops
from .profile import normalize_contact, profile_completion


class RiderProfileSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source='user.name', read_only=True)
    phone = serializers.CharField(source='user.phone_number', read_only=True)
    user_id = serializers.IntegerField(read_only=True)
    profile_completion = serializers.SerializerMethodField()

    def get_profile_completion(self, obj):
        return profile_completion(obj)

    class Meta:
        model = RiderInfo
        fields = ['id', 'user_id', 'name', 'phone', 'is_available', 'score', 'score_updated_at', 'score_version',
                  'job_acceptance_window', 'cancelled_sessions_count', 'last_cancelled_at', 'total_penalties_count',
                  'last_penalty_at', 'penalty_balance', 'penalty_points', 'vehicle_type', 'vehicle_registration',
                  'emergency_contact', 'profile_photo', 'created_at', 'profile_completion']
        read_only_fields = fields


class SessionSerializer(serializers.ModelSerializer):
    vendor = serializers.SerializerMethodField()
    stops = serializers.SerializerMethodField()
    order_count = serializers.SerializerMethodField()
    completed_count = serializers.SerializerMethodField()
    stop_count = serializers.SerializerMethodField()
    estimated_minutes = serializers.SerializerMethodField()
    distance_km = serializers.SerializerMethodField()
    elapsed_seconds = serializers.SerializerMethodField()

    class Meta:
        model = Session
        fields = ['id', 'date', 'vendor', 'status', 'rider_cut', 'service_charge', 'fuel_surge_addition',
            'rider_revenue', 'total_revenue', 'finance_status', 'settled_at', 'session_distance', 'started_at',
            'finished_at', 'pickup_confirmed_at', 'points_awarded', 'order_count', 'completed_count', 'stop_count',
            'estimated_minutes', 'distance_km', 'elapsed_seconds', 'stops']

    def _stops(self, obj):
        if not hasattr(self, '_stop_cache'):
            self._stop_cache = {}
        if obj.pk not in self._stop_cache:
            self._stop_cache[obj.pk] = session_stops(obj)
        return self._stop_cache[obj.pk]

    def get_vendor(self, obj):
        vendor = obj.vendor
        if not vendor:
            return None
        request = self.context.get('request')
        logo = vendor.logo.url if vendor.logo else None
        return {'id': vendor.pk, 'name': vendor.shop_name, 'lat': vendor.lat, 'lng': vendor.lng,
                'logo': request.build_absolute_uri(logo) if logo and request else logo}

    def get_stops(self, obj):
        return self._stops(obj)

    def get_order_count(self, obj):
        return obj.session_orders.count()

    def get_completed_count(self, obj):
        return obj.session_orders.filter(status='delivered').count()

    def get_stop_count(self, obj):
        return len(self._stops(obj))

    def get_estimated_minutes(self, obj):
        return ceil(obj.estimated_duration.total_seconds() / 60) if obj.estimated_duration else None

    def get_distance_km(self, obj):
        if obj.estimated_distance is not None:
            return float(obj.estimated_distance)
        if not obj.vendor:
            return None
        previous, total = (obj.vendor.lat, obj.vendor.lng), 0
        for stop in self._stops(obj):
            total += distance_km(*previous, stop['lat'], stop['lng'])
            previous = stop['lat'], stop['lng']
        return round(total, 3)

    def get_elapsed_seconds(self, obj):
        return int(obj.session_time.total_seconds()) if obj.session_time is not None else None


class JobOfferSerializer(serializers.ModelSerializer):
    session = SessionSerializer(read_only=True)

    class Meta:
        model = JobOffer
        fields = ['id', 'status', 'acceptance_window', 'offered_at', 'expires_at', 'session']


class CancellationSerializer(serializers.ModelSerializer):
    class Meta:
        model = RiderCancellation
        fields = ['id', 'session_reference', 'occurred_at', 'reason', 'details', 'stage', 'released_orders_count']


class PenaltySerializer(serializers.ModelSerializer):
    class Meta:
        model = RiderPenalty
        fields = ['id', 'reference', 'reason', 'amount', 'points', 'status', 'issued_at', 'resolved_at', 'notes']


class ProfileUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False, allow_blank=False)
    vehicle_type = serializers.ChoiceField(choices=['motorcycle', 'scooter', 'bicycle', 'car'], required=False)
    vehicle_registration = serializers.CharField(max_length=30, required=False, allow_blank=True)
    emergency_contact = serializers.CharField(max_length=20, required=False, allow_blank=True)

    def validate_emergency_contact(self, value):
        try:
            return normalize_contact(value)
        except ValueError as error:
            raise serializers.ValidationError(str(error))


class SessionCreateSerializer(serializers.Serializer):
    vendor = serializers.IntegerField(min_value=1)
    orders = serializers.ListField(child=serializers.IntegerField(min_value=1), min_length=1, max_length=100)
    rider_cut = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    service_charge = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    fuel_surge_addition = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, default=0)
    estimated_minutes = serializers.IntegerField(min_value=1, max_value=1440, required=False)
    estimated_distance = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=0, required=False)
    dispatch_enabled = serializers.BooleanField(default=True)
    date = serializers.DateField(required=False)
    offer_to_rider = serializers.IntegerField(min_value=1, required=False,
        help_text='Optional shared rider user ID for the initial offer. The rider still has to accept.')
