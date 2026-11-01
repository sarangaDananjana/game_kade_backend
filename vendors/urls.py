from django.urls import path

from .views import VendorListView, VendorDetailView

app_name = 'vendors'

urlpatterns = [
    path('', VendorListView.as_view(), name='list'),
    path('<int:pk>/', VendorDetailView.as_view(), name='detail'),
]
