from django.urls import path

from .views import (AdminPenaltyView, AdminScoreView, AdminSessionView, AvailabilityView, DashboardView,
    DeliveryFailView, DeliveryVerifyView, HistoryView, JobsView, OfferActionView, RiderProfileView,
    SessionActionView, SessionDetailView, SessionsView)

app_name = 'riders'
urlpatterns = [
    path('profile/', RiderProfileView.as_view(), name='profile'),
    path('availability/', AvailabilityView.as_view(), name='availability'),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
    path('jobs/', JobsView.as_view(), name='jobs'),
    path('jobs/<int:pk>/<str:action>/', OfferActionView.as_view(), name='offer-action'),
    path('sessions/', SessionsView.as_view(), name='sessions'),
    path('sessions/<int:pk>/', SessionDetailView.as_view(), name='session'),
    path('sessions/<int:pk>/orders/<int:order_id>/verify/', DeliveryVerifyView.as_view(), name='verify'),
    path('sessions/<int:pk>/orders/<int:order_id>/failed/', DeliveryFailView.as_view(), name='failed'),
    path('sessions/<int:pk>/<str:action>/', SessionActionView.as_view(), name='session-action'),
    path('history/<str:kind>/', HistoryView.as_view(), name='history'),
    path('admin/sessions/', AdminSessionView.as_view(), name='admin-sessions'),
    path('admin/riders/<int:rider_id>/score/', AdminScoreView.as_view(), name='admin-score'),
    path('admin/riders/<int:rider_id>/penalties/', AdminPenaltyView.as_view(), name='admin-penalty'),
]
