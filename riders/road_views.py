from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from .models import Session
from .road_routes import road_route
from .services import RiderActionError
from .views import RiderAPIView


class RoadRouteView(RiderAPIView):
    def get(self, request, pk):
        session = Session.objects.filter(pk=pk).filter(Q(assigned_rider=request.user)
            | Q(job_offers__rider=request.user, job_offers__status='pending', job_offers__expires_at__gt=timezone.now())).select_related('vendor').distinct().first()
        if session is None:
            raise RiderActionError('This route is not available to you.', 'session_not_found', 404)
        return Response(road_route(session))
