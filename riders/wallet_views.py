from rest_framework.response import Response
from rest_framework import serializers
from .models import RiderTopUp, RiderWalletEntry
from .views import RiderAPIView


class WalletEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = RiderWalletEntry
        fields = ['id', 'kind', 'amount', 'balance_after', 'session_reference', 'created_at']


class WalletView(RiderAPIView):
    def get(self, request):
        profile = self.profile(request)
        return Response({'balance': str(profile.topup_balance), 'acceptance_percent': 20,
            'transactions': WalletEntrySerializer(profile.wallet_entries.all()[:100], many=True).data})
