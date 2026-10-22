"""Message history and outbound text-message endpoints."""

from django.core.exceptions import ValidationError
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_201_CREATED, HTTP_400_BAD_REQUEST
from rest_framework.views import APIView

from whatsapp.meta.client import MetaWhatsAppAPIError
from whatsapp.models import Message
from whatsapp.operations.messages.outbound import send_outbound_whatsapp_text_message
from whatsapp.serializers.business import (
    MessageSerializer,
    OutboundTextMessageSerializer,
)
from whatsapp.utils.pagination import MessagePagination
from whatsapp.utils.permissions import IsMember, IsMonitoringUser
from whatsapp.utils.resolvers import (
    resolve_conversation_message,
    resolve_monitoring_whatsapp_number,
    resolve_whatsapp_number_conversation,
)


class MessageView(APIView):
    """
        DOCSTRING: Message View

        Description:
        - Return paginated persisted monitored messages belonging to a specific conversation.
        - Return detailed information for a specific monitored message.

        Notes:
        - Company, number, conversation, and message are resolved hierarchically.
        - Foreign message identifiers return not found.
    """

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated, IsMonitoringUser]
    business_serializer = MessageSerializer
    pagination_class = MessagePagination

    def get(self, request, company_id, branch_id, number_id, conversation_id, message_id=None):
        whatsapp_number = resolve_monitoring_whatsapp_number(user=request.user, company_id=company_id, branch_id=branch_id, number_id=number_id)
        conversation = resolve_whatsapp_number_conversation(whatsapp_number=whatsapp_number, conversation_id=conversation_id)

        messages = Message.objects.select_related(
            "context_message",
            "conversation",
        ).prefetch_related(
            "media_attachments",
        ).filter(
            conversation=conversation,
            conversation__whatsapp_number=whatsapp_number,
            conversation__customer__company=whatsapp_number.company,
            is_active=True,
        )

        if message_id:
            message = resolve_conversation_message(conversation=conversation, message_id=message_id)

            response_payload = {
                "success_message": "Mensaje extraído correctamente.",
                "data": self.business_serializer(message).data,
            }

            return Response(response_payload, status=HTTP_200_OK)

        ordering = request.query_params.get("ordering", "message_timestamp")
        allowed_ordering = ["message_timestamp", "-message_timestamp"]

        if ordering not in allowed_ordering:
            response_payload = {
                "error_message": "Ordenamiento de mensajes rechazado: el criterio proporcionado no es válido.",
                "data": {
                    "ordering": f"Valores permitidos: {', '.join(allowed_ordering)}.",
                },
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        messages = messages.order_by(
            ordering,
            "id" if ordering == "message_timestamp" else "-id",
        )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(messages, request, view=self)
        response_data = self.business_serializer(page, many=True).data

        return paginator.get_paginated_response(response_data)


class OutboundMessageView(APIView):
    """
        DOCSTRING: Outbound Message View

        Description:
        - Send a plain-text WhatsApp message from a monitored Dialoqo conversation.

        Notes:
        - The authenticated user must have active access to the requested company.
        - Number and conversation are resolved inside that company tenant.
        - Destination and source phone numbers are never accepted from request data.
        - A foreign conversation identifier cannot be used to send through another tenant.
        - Successful message-send auditing is implemented inside the operation layer.
    """

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated, IsMember]
    input_serializer = OutboundTextMessageSerializer
    output_serializer = MessageSerializer

    def post(self, request, company_id, branch_id, number_id, conversation_id):
        whatsapp_number = resolve_monitoring_whatsapp_number(user=request.user, company_id=company_id, branch_id=branch_id, number_id=number_id)
        conversation = resolve_whatsapp_number_conversation(whatsapp_number=whatsapp_number, conversation_id=conversation_id)
        serializer = self.input_serializer(data=request.data)

        if not serializer.is_valid():
            response_payload = {
                "error_message": "Envío de mensaje rechazado: los datos proporcionados no son válidos.",
                "data": serializer.errors,
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        try:
            result = send_outbound_whatsapp_text_message(
                conversation=conversation,
                text_body=serializer.validated_data["text_body"],
                actor=request.user,
            )

        except ValidationError as error:
            response_payload = {
                "error_message": "Envío de mensaje rechazado: no fue posible completar la operación.",
                "data": error.message_dict,
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        except MetaWhatsAppAPIError as error:
            response_payload = {
                "error_message": "Envío de mensaje rechazado: no fue posible completar el envío.",
                "data": {
                    "meta": {
                        "http_status": error.http_status,
                        "error_code": error.meta_error_code,
                        "error_subcode": error.meta_error_subcode,
                        "message": error.error_message,
                    },
                },
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        message = result["message"]

        response_payload = {
            "success_message": "Mensaje enviado a Meta correctamente.",
            "data": self.output_serializer(message).data,
        }

        return Response(response_payload, status=HTTP_201_CREATED)
