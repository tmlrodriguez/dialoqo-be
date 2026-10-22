from rest_framework.permissions import BasePermission

from access.registry import ROLE_REGISTRY

# Define your WhatsApp permissions here.

class IsMonitoringUser(BasePermission):
    """
        DOCSTRING: Is Monitoring User Permission

        Description:
        - Allow authenticated and active MONITOR and MEMBER users to access monitoring resources.

        Notes:
        - Resource-level authorization is enforced independently through tenant-safe resolvers.
        - MONITOR users remain read-only for conversation content.
        - MEMBER users may operate only the WhatsApp numbers currently assigned to them.
    """

    message = "Access rejected: monitoring user permission is required."

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_active and request.user.role in [ROLE_REGISTRY.MONITOR, ROLE_REGISTRY.MEMBER]


class IsMember(BasePermission):
    """
        DOCSTRING: Is Member Permission

        Description:
        - Restrict operational WhatsApp messaging actions to authenticated and active MEMBER users.

        Notes:
        - Number assignment authorization is enforced independently through tenant-safe resolvers and business operations.
        - MONITOR users cannot send messages, templates, or initiate conversations.
    """

    message = "Access rejected: member permission is required."

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_active and request.user.role == ROLE_REGISTRY.MEMBER
