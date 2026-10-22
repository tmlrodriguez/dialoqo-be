from types import SimpleNamespace

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from access.registry import ROLE_REGISTRY
from organizations.models import UserCompanyAccess

from whatsapp.models import NumberAssignment, WhatsAppNumber
from whatsapp.realtime.events import (
    get_whatsapp_number_group_name,
    get_whatsapp_user_group_name,
)
from whatsapp.utils.permissions import IsMonitoringUser

# Define your WebSocket consumers here.

class WhatsAppMonitorConsumer(AsyncJsonWebsocketConsumer):
    """
        DOCSTRING: WhatsApp Monitor Consumer

        Description:
        - Provide the authenticated realtime WebSocket connection used by Dialoqo MONITOR and MEMBER users.
        - Subscribe an authorized user to one company-owned WhatsApp number.
        - Forward normalized backend events to the connected frontend.

        Notes:
        - Authentication is resolved before the consumer through TokenQueryAuthMiddleware.
        - MONITOR users are authorized through active company access.
        - MEMBER users are authorized through the active assignment of the requested WhatsApp number.
        - The WhatsApp number must remain active, connected, and monitoring-enabled.
        - Each connection joins a shared number group and a private user-specific group.
        - Clients do not modify business data through this WebSocket; business writes remain REST operations.
    """

    async def connect(self):
        user = self.scope.get("user")

        if user is None or not user.is_authenticated:
            await self.close(code=4401)
            return

        company_id = self.scope["url_route"]["kwargs"]["company_id"]
        whatsapp_number_id = self.scope["url_route"]["kwargs"]["number_id"]
        authorized = await self.authorize_connection(user=user, company_id=company_id, whatsapp_number_id=whatsapp_number_id)

        if not authorized:
            await self.close(code=4403)
            return

        self.company_id = company_id
        self.whatsapp_number_id = whatsapp_number_id
        self.number_group_name = get_whatsapp_number_group_name(company_id=company_id, whatsapp_number_id=whatsapp_number_id)
        self.user_group_name = get_whatsapp_user_group_name(user_id=user.id, company_id=company_id, whatsapp_number_id=whatsapp_number_id)

        await self.channel_layer.group_add(self.number_group_name, self.channel_name)
        await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        await self.accept()
        await self.send_json({"event": "connection.ready", "data": {"company_id": company_id, "whatsapp_number_id": whatsapp_number_id}})

    async def disconnect(self, close_code):
        if hasattr(self, "number_group_name"):
            await self.channel_layer.group_discard(self.number_group_name, self.channel_name)

        if hasattr(self, "user_group_name"):
            await self.channel_layer.group_discard(self.user_group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        """
            DOCSTRING: Receive JSON

            Description:
            - Process limited client-to-server WebSocket control messages.

            Notes:
            - Business mutations are intentionally rejected through the WebSocket transport.
            - ping is supported so clients can verify connection liveness.
        """

        event_name = content.get("event") if isinstance(content, dict) else None

        if event_name == "ping":
            await self.send_json({"event": "pong", "data": {}})
            return

        await self.send_json({"event": "error", "data": {"message": "Operación WebSocket rechazada: el evento solicitado no es válido."}})

    async def realtime_event(self, event):
        """
            DOCSTRING: Realtime Event

            Description:
            - Forward a normalized backend realtime event to the connected monitoring user.
            - Revalidate role-specific number authorization before every event is delivered.

            Notes:
            - Revalidation prevents a MEMBER whose number assignment was revoked or reassigned from continuing to receive events through an already-open socket.
            - Unauthorized sockets are closed before the event payload is delivered.
        """

        user = self.scope.get("user")
        authorized = await self.authorize_connection(user=user, company_id=self.company_id, whatsapp_number_id=self.whatsapp_number_id)

        if not authorized:
            await self.close(code=4403)
            return

        await self.send_json({"event": event["event"], "data": event.get("data", {})})

    @database_sync_to_async
    def authorize_connection(self, user, company_id, whatsapp_number_id):
        """
            DOCSTRING: Authorize Connection

            Description:
            - Validate monitoring permission and role-specific WhatsApp-number authorization before joining realtime groups.

            Notes:
            - MONITOR users require active access to the requested company.
            - MEMBER users require the active assignment of the requested WhatsApp number.
            - Cross-company and cross-number subscription attempts are rejected.
        """

        permission_request = SimpleNamespace(user=user)

        if not IsMonitoringUser().has_permission(permission_request, None):
            return False

        number_exists = WhatsAppNumber.objects.filter(
            id=whatsapp_number_id,
            company_id=company_id,
            company__is_active=True,
            branch__company_id=company_id,
            branch__is_active=True,
            whatsapp_business_account__company_id=company_id,
            whatsapp_business_account__is_active=True,
            is_active=True,
            is_connected=True,
            is_monitoring_enabled=True,
        ).exists()

        if not number_exists:
            return False

        if user.role == ROLE_REGISTRY.MONITOR:
            return UserCompanyAccess.objects.filter(
                user=user,
                company_id=company_id,
                is_active=True,
                company__is_active=True,
            ).exists()

        if user.role == ROLE_REGISTRY.MEMBER:
            return NumberAssignment.objects.filter(
                member=user,
                whatsapp_number_id=whatsapp_number_id,
                whatsapp_number__company_id=company_id,
                is_active=True,
                unassigned_at__isnull=True,
            ).exists()

        return False
