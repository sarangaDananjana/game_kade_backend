from rest_framework.permissions import BasePermission, SAFE_METHODS


def is_platform_admin(user):
    return user.is_authenticated and (
        user.is_staff or user.is_superuser or getattr(user, 'role', '') == 'admin'
    )


class CanManageProducts(BasePermission):
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        user = request.user
        return user.is_authenticated and (is_platform_admin(user) or user.shops.exists())

    def has_object_permission(self, request, view, obj):
        return request.method in SAFE_METHODS or is_platform_admin(request.user) or obj.vendor.owner_id == request.user.pk


class IsPlatformAdmin(BasePermission):
    def has_permission(self, request, view):
        return is_platform_admin(request.user)


class CanManageDeliveryZones(BasePermission):
    def has_permission(self, request, view):
        return request.method in SAFE_METHODS or is_platform_admin(request.user)
