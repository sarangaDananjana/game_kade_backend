from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError as ModelValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from vendors.permissions import IsPlatformAdmin
from .models import JobOffer, RiderInfo, Session
from .serializers import (CancellationSerializer, JobOfferSerializer, PenaltySerializer,
    ProfileUpdateSerializer, RiderProfileSerializer, SessionCreateSerializer, SessionSerializer)
from .services import (RiderActionError, accept_offer, cancel_session, create_session, decline_offer,
    dispatch_due, fail_delivery, issue_penalty, start_session, verify_delivery)
from .timeutils import month_dates, period_bounds, rider_local_date, week_dates


class IsRider(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_active and request.user.role == 'rider'


class RiderAPIView(APIView):
    permission_classes = [IsRider]

    def handle_exception(self, exc):
        if isinstance(exc, RiderActionError):
            return Response({'detail': str(exc), 'code': exc.code}, status=exc.status)
        if isinstance(exc, ModelValidationError):
            return Response({'detail': '; '.join(exc.messages), 'code': 'invalid_rider_data'}, status=400)
        return super().handle_exception(exc)

    def profile(self, request):
        return RiderInfo.objects.select_related('user').get(user=request.user)


class RiderProfileView(RiderAPIView):
    def get(self, request):
        return Response(RiderProfileSerializer(self.profile(request), context={'request': request}).data)

    @transaction.atomic
    def patch(self, request):
        data = ProfileUpdateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        profile = RiderInfo.objects.select_for_update().select_related('user').get(user=request.user)
        values = dict(data.validated_data)
        if 'name' in values:
            profile.user.name = values.pop('name')
            profile.user.save(update_fields=['name'])
        for field, value in values.items():
            setattr(profile, field, value)
        profile.save()
        return Response(RiderProfileSerializer(profile, context={'request': request}).data)


class AvailabilityView(RiderAPIView):
    def post(self, request):
        class Input(serializers.Serializer):
            is_available = serializers.BooleanField()
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        with transaction.atomic():
            profile = RiderInfo.objects.select_for_update().get(user=request.user)
            profile.is_available = data.validated_data['is_available']
            profile.save(update_fields=['is_available', 'updated_at'])
        # Retained for older clients. This flag no longer gates or withdraws job offers.
        return Response(RiderProfileSerializer(self.profile(request), context={'request': request}).data)


class DashboardView(RiderAPIView):
    def get(self, request):
        profile = self.profile(request)
        period = request.query_params.get('period', 'week')
        today = rider_local_date()
        if period == 'today':
            begin, end = today, today + timedelta(days=1)
        elif period == 'week':
            begin, end = week_dates(today)
        elif period == 'month':
            begin, end = month_dates(today)
        elif period == 'lifetime':
            begin = end = None
        else:
            raise RiderActionError('Choose today, week, month, or lifetime.', 'invalid_period', 400)
        summary = profile.stats(begin, end)
        cancellations = profile.cancellations.all()
        penalties = profile.penalties.all()
        if begin:
            start_at, end_at = period_bounds(begin, end)
            cancellations = cancellations.filter(occurred_at__gte=start_at, occurred_at__lt=end_at)
            penalties = penalties.filter(issued_at__gte=start_at, issued_at__lt=end_at)
        summary.update({'cancelled_sessions': cancellations.count(), 'penalties_count': penalties.count(),
            'penalty_amount': penalties.exclude(status='reversed').aggregate(total=Sum('amount', default=Decimal('0.00')))['total'],
            'score': profile.score, 'job_acceptance_window': profile.job_acceptance_window})
        series = []
        for offset in range(6, -1, -1):
            day = today - timedelta(days=offset)
            daily = profile.stats(day, day + timedelta(days=1))
            series.append({'date': day, 'revenue': daily['rider_revenue'], 'orders': daily['delivered_orders']})
        active = Session.objects.filter(assigned_rider=request.user, status__in=['accepted', 'in_progress']).select_related('vendor').first()
        summary['session_time_seconds'] = int(summary['session_time'].total_seconds())
        summary['average_session_time_seconds'] = int(summary['average_session_time'].total_seconds()) if summary['average_session_time'] else None
        return Response({'period': period, 'profile': RiderProfileSerializer(profile, context={'request': request}).data,
            'stats': summary, 'earnings_series': series,
            'active_session': SessionSerializer(active, context={'request': request}).data if active else None,
            'cancellations': CancellationSerializer(profile.cancellations.all()[:5], many=True).data,
            'penalties': PenaltySerializer(profile.penalties.all()[:5], many=True).data, 'server_time': timezone.now()})


class JobsView(RiderAPIView):
    def get(self, request):
        dispatch_due()
        offers = JobOffer.objects.filter(rider=request.user, status='pending', expires_at__gt=timezone.now()).select_related('session__vendor')
        return Response({'offers': JobOfferSerializer(offers, many=True, context={'request': request}).data, 'server_time': timezone.now()})


class OfferActionView(RiderAPIView):
    def post(self, request, pk, action):
        if action == 'accept':
            session = accept_offer(request.user, pk)
            return Response(SessionSerializer(session, context={'request': request}).data)
        if action == 'decline':
            reason = str(request.data.get('reason', ''))[:255]
            decline_offer(request.user, pk, reason)
            return Response({'detail': 'Offer declined. You can receive another job.'})
        raise RiderActionError('Choose accept or decline.', 'invalid_action', 400)


class SessionsView(RiderAPIView):
    def get(self, request):
        sessions = Session.objects.filter(assigned_rider=request.user).select_related('vendor').order_by('-created_at')
        active = sessions.filter(status__in=['accepted', 'in_progress'])
        past = sessions.filter(status__in=['completed', 'cancelled'])[:50]
        return Response({'active': SessionSerializer(active, many=True, context={'request': request}).data,
            'past': SessionSerializer(past, many=True, context={'request': request}).data, 'server_time': timezone.now()})


class SessionDetailView(RiderAPIView):
    def get(self, request, pk):
        session = Session.objects.filter(pk=pk, assigned_rider=request.user).select_related('vendor').first()
        if session is None:
            raise RiderActionError('This session is not assigned to you.', 'session_not_found', 404)
        return Response(SessionSerializer(session, context={'request': request}).data)


class SessionActionView(RiderAPIView):
    def post(self, request, pk, action):
        if action == 'pickup':
            session = start_session(request.user, pk)
            return Response(SessionSerializer(session, context={'request': request}).data)
        if action == 'cancel':
            class Input(serializers.Serializer):
                reason = serializers.ChoiceField(choices=['vehicle_issue', 'personal_emergency', 'route_problem', 'customer_issue', 'other'])
                details = serializers.CharField(max_length=1000, required=False, allow_blank=True, default='')
            data = Input(data=request.data)
            data.is_valid(raise_exception=True)
            event = cancel_session(request.user, pk, **data.validated_data)
            return Response(CancellationSerializer(event).data)
        raise RiderActionError('Choose pickup or cancel.', 'invalid_action', 400)


class DeliveryVerifyView(RiderAPIView):
    def post(self, request, pk, order_id):
        class Input(serializers.Serializer):
            code = serializers.CharField(max_length=50)
            distance_km = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=0, required=False)
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        session = verify_delivery(request.user, pk, order_id, **data.validated_data)
        return Response(SessionSerializer(session, context={'request': request}).data)


class DeliveryFailView(RiderAPIView):
    def post(self, request, pk, order_id):
        class Input(serializers.Serializer):
            reason = serializers.CharField(max_length=1000, allow_blank=False)
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        session = fail_delivery(request.user, pk, order_id, data.validated_data['reason'])
        return Response(SessionSerializer(session, context={'request': request}).data)


class HistoryView(RiderAPIView):
    def get(self, request, kind):
        profile = self.profile(request)
        if kind == 'cancellations':
            return Response(CancellationSerializer(profile.cancellations.all()[:100], many=True).data)
        if kind == 'penalties':
            return Response(PenaltySerializer(profile.penalties.all()[:100], many=True).data)
        raise RiderActionError('This history is not available.', 'history_not_found', 404)


class AdminSessionView(RiderAPIView):
    permission_classes = [IsPlatformAdmin]

    def post(self, request):
        data = SessionCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = dict(data.validated_data)
        session = create_session(values.pop('vendor'), values.pop('orders'), **values)
        return Response(SessionSerializer(session, context={'request': request}).data, status=201)

    def get(self, request):
        return Response(SessionSerializer(Session.objects.select_related('vendor').all()[:100], many=True, context={'request': request}).data)


class AdminScoreView(RiderAPIView):
    permission_classes = [IsPlatformAdmin]

    @transaction.atomic
    def post(self, request, rider_id):
        class Input(serializers.Serializer):
            score = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100)
            job_acceptance_window = serializers.IntegerField(min_value=5, max_value=600)
            score_version = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        profile = RiderInfo.objects.select_for_update(of=('self',)).filter(user_id=rider_id, user__role='rider').first()
        if profile is None:
            raise RiderActionError('This rider does not exist.', 'rider_not_found', 404)
        for name, value in data.validated_data.items():
            setattr(profile, name, value)
        profile.score_updated_at = timezone.now()
        profile.save(update_fields=[*data.validated_data, 'score_updated_at', 'updated_at'])
        return Response(RiderProfileSerializer(profile).data)


class AdminPenaltyView(RiderAPIView):
    permission_classes = [IsPlatformAdmin]

    def post(self, request, rider_id):
        class Input(serializers.Serializer):
            reference = serializers.CharField(max_length=120)
            reason = serializers.CharField(max_length=255)
            amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, default=0)
            points = serializers.IntegerField(min_value=0, default=0)
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        profile = RiderInfo.objects.filter(user_id=rider_id).first()
        if profile is None:
            raise RiderActionError('This rider does not exist.', 'rider_not_found', 404)
        penalty = issue_penalty(profile, **data.validated_data)
        return Response(PenaltySerializer(penalty).data, status=201)
