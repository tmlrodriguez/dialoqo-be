"""Conversation history and read-state endpoints."""

from django.db.models import Prefetch, Q
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_400_BAD_REQUEST
from rest_framework.views import APIView

from whatsapp.models import Conversation, ConversationReadState
from whatsapp.operations.assignments import get_current_whatsapp_number_assignment
from whatsapp.operations.conversations import mark_conversation_as_read
from whatsapp.serializers.business import ConversationSerializer
from whatsapp.serializers.snapshots import (
    ConversationReadStateSnapshotSerializer,
    ConversationSnapshotSerializer,
)
from whatsapp.utils.pagination import ConversationPagination
from whatsapp.utils.permissions import IsMonitoringUser
from whatsapp.utils.resolvers import (
    resolve_monitoring_whatsapp_number,
    resolve_whatsapp_number_conversation,
)


class ConversationView(APIView):
    """
        DOCSTRING: Conversation View

        Description:
        - Return paginated monitored conversations belonging to a corporate WhatsApp number.
        - Return detailed information for a specific monitored conversation.
        - Support search, unread filtering, current-member assignment filtering, and explicit ordering.

        Notes:
        - MONITOR and MEMBER users may access conversation content according to their role-specific authorization.
        - MONITOR users are authorized through company access.
        - MEMBER users are authorized through the active assignment of the requested WhatsApp number.
        - Conversations are resolved inside that number and company.
        - Foreign conversation identifiers therefore behave as nonexistent.
    """

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated, IsMonitoringUser]
    business_serializer = ConversationSerializer
    snapshot_serializer = ConversationSnapshotSerializer
    pagination_class = ConversationPagination

    def get(self, request, company_id, branch_id, number_id, conversation_id=None):
        whatsapp_number = resolve_monitoring_whatsapp_number(user=request.user, company_id=company_id, branch_id=branch_id, number_id=number_id)
        current_assignment = get_current_whatsapp_number_assignment(whatsapp_number=whatsapp_number)

        if conversation_id:
            conversation = resolve_whatsapp_number_conversation(whatsapp_number=whatsapp_number, conversation_id=conversation_id)

            response_payload = {
                "success_message": "Conversación extraída correctamente.",
                "data": self.business_serializer(
                    conversation,
                    context={
                        "request": request,
                        "current_assignment": current_assignment,
                    },
                ).data,
            }

            return Response(response_payload, status=HTTP_200_OK)

        conversations = Conversation.objects.select_related(
            "customer",
            "last_message",
            "last_message__context_message",
        ).prefetch_related(
            "last_message__media_attachments",
            Prefetch(
                "read_states",
                queryset=ConversationReadState.objects.filter(user=request.user),
                to_attr="current_user_read_states",
            ),
        ).filter(
            whatsapp_number=whatsapp_number,
            whatsapp_number__company=whatsapp_number.company,
            customer__company=whatsapp_number.company,
            is_active=True,
        )

        search = request.query_params.get("search", "").strip()
        unread = request.query_params.get("unread")
        member_id = request.query_params.get("member_id")
        ordering = request.query_params.get("ordering", "-last_message_at")

        if search:
            conversations = conversations.filter(
                Q(customer__phone_number__icontains=search)
                | Q(customer__display_name__icontains=search)
                | Q(customer__profile_name__icontains=search)
            )

        if unread is not None:
            normalized_unread = unread.strip().lower()

            if normalized_unread in ["true", "1", "yes"]:
                conversations = conversations.filter(
                    read_states__user=request.user,
                    read_states__is_read=False,
                    read_states__unread_count__gt=0,
                )

            elif normalized_unread in ["false", "0", "no"]:
                conversations = conversations.filter(
                    Q(read_states__user=request.user, read_states__is_read=True)
                    | Q(read_states__user=request.user, read_states__unread_count=0)
                    | ~Q(read_states__user=request.user)
                )

            else:
                response_payload = {
                    "error_message": "Filtro de conversaciones rechazado: unread contiene un valor inválido.",
                    "data": {
                        "unread": "Valores permitidos: true, false, 1, 0, yes, no.",
                    },
                }

                return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        if member_id is not None:
            try:
                member_id = int(member_id)

            except (TypeError, ValueError):
                response_payload = {
                    "error_message": "Filtro de conversaciones rechazado: member_id no contiene un identificador válido.",
                    "data": {
                        "member_id": "Debe proporcionar un identificador numérico válido.",
                    },
                }

                return Response(response_payload, status=HTTP_400_BAD_REQUEST)

            if current_assignment is None or current_assignment.member_id != member_id:
                conversations = conversations.none()

        allowed_ordering = ["last_message_at", "-last_message_at", "created_at", "-created_at"]

        if ordering not in allowed_ordering:
            response_payload = {
                "error_message": "Ordenamiento de conversaciones rechazado: el criterio proporcionado no es válido.",
                "data": {
                    "ordering": f"Valores permitidos: {', '.join(allowed_ordering)}.",
                },
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        conversations = conversations.order_by(ordering, "-id").distinct()
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(conversations, request, view=self)

        for conversation in page:
            conversation.current_user_read_state = conversation.current_user_read_states[0] if conversation.current_user_read_states else None

        response_data = self.snapshot_serializer(
            page,
            many=True,
            context={
                "request": request,
                "current_assignment": current_assignment,
            },
        ).data

        return paginator.get_paginated_response(response_data)


class ConversationReadView(APIView):
    """
        DOCSTRING: Conversation Read View

        Description:
        - Mark a monitored conversation as read for the authenticated monitoring user.

        Notes:
        - Company, number, and conversation resolution remain tenant-scoped.
        - A conversation belonging to another company or number cannot be marked as read.
        - Read acknowledgement auditing is implemented inside the operation layer.
    """

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated, IsMonitoringUser]
    snapshot_serializer = ConversationReadStateSnapshotSerializer

    def post(self, request, company_id, branch_id, number_id, conversation_id):
        whatsapp_number = resolve_monitoring_whatsapp_number(user=request.user, company_id=company_id, branch_id=branch_id, number_id=number_id)
        conversation = resolve_whatsapp_number_conversation(whatsapp_number=whatsapp_number, conversation_id=conversation_id)
        read_state = mark_conversation_as_read(conversation=conversation, user=request.user)

        response_payload = {
            "success_message": "Conversación marcada como leída correctamente.",
            "data": self.snapshot_serializer(read_state).data,
        }

        return Response(response_payload, status=HTTP_200_OK)
