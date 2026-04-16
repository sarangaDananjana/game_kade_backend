from rest_framework import viewsets, serializers
from rest_framework.permissions import IsAuthenticated
from .models import OrderLocation, Cart, CartItem, Order


class OrderLocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderLocation
        fields = '__all__'


class OrderLocationViewSet(viewsets.ModelViewSet):
    serializer_class = OrderLocationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Return system-defined locations PLUS the current user's locations
        user = self.request.user
        return OrderLocation.objects.filter(models.Q(is_system_defined=True) | models.Q(user=user))

    def perform_create(self, serializer):
        # Automatically assign the logged-in user to the location they are creating
        serializer.save(user=self.request.user, is_system_defined=False)

# I have included the locations here. You can expand on Carts and Orders similarly!
