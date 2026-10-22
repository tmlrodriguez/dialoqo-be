from django.core.exceptions import ValidationError
from django.db.models import Q
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_201_CREATED, HTTP_400_BAD_REQUEST
from rest_framework.views import APIView

from organizations.permissions import IsOrganizationAdministrator

from whatsapp.meta.client import MetaWhatsAppAPIError
from whatsapp.models import WhatsAppMessageTemplate
from whatsapp.operations.templates import (
    create_whatsapp_message_template,
    delete_whatsapp_message_template,
    send_template_to_existing_conversation,
    send_template_to_new_conversation,
    synchronize_whatsapp_message_templates,
    update_whatsapp_message_template,
)
from whatsapp.registry import MESSAGE_TEMPLATE_STATUS_REGISTRY
from whatsapp.serializers.business import MessageSerializer
from whatsapp.serializers.templates import (
    NewConversationTemplateSendSerializer,
    TemplateSendSerializer,
    WhatsAppMessageTemplateCreateSerializer,
    WhatsAppMessageTemplateSerializer,
    WhatsAppMessageTemplateSnapshotSerializer,
    WhatsAppMessageTemplateUpdateSerializer,
)
from whatsapp.utils.pagination import MessageTemplatePagination
from whatsapp.utils.permissions import IsMember
from whatsapp.utils.resolvers import (
    resolve_administrative_company,
    resolve_company_whatsapp_business_account,
    resolve_monitoring_whatsapp_number,
    resolve_waba_message_template,
    resolve_whatsapp_number_conversation,
)

# Define your template views here.

class MessageTemplateView(APIView):
    """
        DOCSTRING: Message Template View

        Description:
        - Return, create, update, and delete WhatsApp message templates for an administratively managed WABA.

        Notes:
        - Company ownership is resolved before WABA ownership.
        - Template ownership is resolved inside the WABA.
        - Foreign template and WABA identifiers return not found.
    """

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated, IsOrganizationAdministrator]
    serializer_class = WhatsAppMessageTemplateSerializer
    pagination_class = MessageTemplatePagination

    def get(self, request, company_id, account_id, template_id=None):
        company = resolve_administrative_company(user=request.user, company_id=company_id)
        account = resolve_company_whatsapp_business_account(company=company, account_id=account_id)

        templates = WhatsAppMessageTemplate.objects.filter(
            whatsapp_business_account=account,
            whatsapp_business_account__company=company,
            is_active=True,
        )

        if template_id:
            template = resolve_waba_message_template(whatsapp_business_account=account, template_id=template_id)

            return Response(
                {
                    "success_message": "Plantilla de WhatsApp extraída correctamente.",
                    "data": self.serializer_class(template).data,
                },
                status=HTTP_200_OK,
            )

        search = request.query_params.get("search", "").strip()
        status_filter = request.query_params.get("status")
        category = request.query_params.get("category")
        language = request.query_params.get("language")

        if search:
            templates = templates.filter(Q(name__icontains=search) | Q(language__icontains=search))

        if status_filter:
            templates = templates.filter(status=status_filter.upper())

        if category:
            templates = templates.filter(category=category.upper())

        if language:
            templates = templates.filter(language=language)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(templates.order_by("name", "language"), request, view=self)
        response_data = self.serializer_class(page, many=True).data

        return paginator.get_paginated_response(response_data)

    def post(self, request, company_id, account_id):
        company = resolve_administrative_company(user=request.user, company_id=company_id)
        account = resolve_company_whatsapp_business_account(company=company, account_id=account_id)

        serializer = WhatsAppMessageTemplateCreateSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                {
                    "error_message": "Creación de plantilla rechazada: los datos proporcionados no son válidos.",
                    "data": serializer.errors,
                },
                status=HTTP_400_BAD_REQUEST,
            )

        try:
            template = create_whatsapp_message_template(whatsapp_business_account=account, validated_data=serializer.validated_data, actor=request.user)

        except MetaWhatsAppAPIError as error:
            return Response(
                {
                    "error_message": "Creación de plantilla rechazada: Meta no aceptó la plantilla.",
                    "data": {
                        "meta": {
                            "http_status": error.http_status,
                            "error_code": error.meta_error_code,
                            "error_subcode": error.meta_error_subcode,
                            "message": error.error_message,
                        },
                    },
                },
                status=HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "success_message": "Plantilla enviada a Meta correctamente.",
                "data": self.serializer_class(template).data if template else {},
            },
            status=HTTP_201_CREATED,
        )

    def patch(self, request, company_id, account_id, template_id):
        company = resolve_administrative_company(user=request.user, company_id=company_id)
        account = resolve_company_whatsapp_business_account(company=company, account_id=account_id)
        template = resolve_waba_message_template(whatsapp_business_account=account, template_id=template_id)

        serializer = WhatsAppMessageTemplateUpdateSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                {
                    "error_message": "Actualización de plantilla rechazada: los datos proporcionados no son válidos.",
                    "data": serializer.errors,
                },
                status=HTTP_400_BAD_REQUEST,
            )

        try:
            template = update_whatsapp_message_template(whatsapp_message_template=template, validated_data=serializer.validated_data, actor=request.user)

        except MetaWhatsAppAPIError as error:
            return Response(
                {
                    "error_message": "Actualización de plantilla rechazada: Meta no aceptó los cambios.",
                    "data": {
                        "meta": {
                            "http_status": error.http_status,
                            "error_code": error.meta_error_code,
                            "error_subcode": error.meta_error_subcode,
                            "message": error.error_message,
                        },
                    },
                },
                status=HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "success_message": "Plantilla actualizada correctamente.",
                "data": self.serializer_class(template).data,
            },
            status=HTTP_200_OK,
        )

    def delete(self, request, company_id, account_id, template_id):
        company = resolve_administrative_company(user=request.user, company_id=company_id)
        account = resolve_company_whatsapp_business_account(company=company, account_id=account_id)
        template = resolve_waba_message_template(whatsapp_business_account=account, template_id=template_id)

        try:
            template = delete_whatsapp_message_template(whatsapp_message_template=template, actor=request.user)

        except MetaWhatsAppAPIError as error:
            return Response(
                {
                    "error_message": "Eliminación de plantilla rechazada: Meta no aceptó la operación.",
                    "data": {
                        "meta": {
                            "http_status": error.http_status,
                            "error_code": error.meta_error_code,
                            "error_subcode": error.meta_error_subcode,
                            "message": error.error_message,
                        },
                    },
                },
                status=HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "success_message": "Plantilla eliminada correctamente.",
                "data": self.serializer_class(template).data,
            },
            status=HTTP_200_OK,
        )


class MessageTemplateSyncView(APIView):
    """
        DOCSTRING: Message Template Sync View

        Description:
        - Synchronize the complete Meta template catalog for a tenant-scoped WABA.

        Notes:
        - Company ownership and WABA ownership are resolved before synchronization.
    """

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated, IsOrganizationAdministrator]

    def post(self, request, company_id, account_id):
        company = resolve_administrative_company(user=request.user, company_id=company_id)
        account = resolve_company_whatsapp_business_account(company=company, account_id=account_id)

        try:
            result = synchronize_whatsapp_message_templates(whatsapp_business_account=account, actor=request.user)

        except MetaWhatsAppAPIError as error:
            return Response(
                {
                    "error_message": "Sincronización de plantillas rechazada: Meta no pudo procesar la solicitud.",
                    "data": {
                        "meta": {
                            "http_status": error.http_status,
                            "error_code": error.meta_error_code,
                            "error_subcode": error.meta_error_subcode,
                            "message": error.error_message,
                        },
                    },
                },
                status=HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "success_message": "Plantillas sincronizadas correctamente.",
                "data": result,
            },
            status=HTTP_200_OK,
        )


class AvailableMessageTemplateView(APIView):
    """
        DOCSTRING: Available Message Template View

        Description:
        - Return approved templates available to a MEMBER user for a currently assigned WhatsApp number.

        Notes:
        - The current MEMBER assignment is resolved before the WhatsApp number is exposed.
        - The template catalog is restricted to the exact WABA attached to that number.
    """

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated, IsMember]
    serializer_class = WhatsAppMessageTemplateSnapshotSerializer
    pagination_class = MessageTemplatePagination

    def get(self, request, company_id, branch_id, number_id):
        whatsapp_number = resolve_monitoring_whatsapp_number(user=request.user, company_id=company_id, branch_id=branch_id, number_id=number_id)

        templates = WhatsAppMessageTemplate.objects.filter(
            whatsapp_business_account=whatsapp_number.whatsapp_business_account,
            whatsapp_business_account__company=whatsapp_number.company,
            status=MESSAGE_TEMPLATE_STATUS_REGISTRY.APPROVED,
            is_available_in_meta=True,
            is_active=True,
        )

        search = request.query_params.get("search", "").strip()
        category = request.query_params.get("category")
        language = request.query_params.get("language")

        if search:
            templates = templates.filter(name__icontains=search)

        if category:
            templates = templates.filter(category=category.upper())

        if language:
            templates = templates.filter(language=language)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(templates.order_by("name", "language"), request, view=self)

        return paginator.get_paginated_response(self.serializer_class(page, many=True).data)


class ConversationTemplateSendView(APIView):
    """
        DOCSTRING: Conversation Template Send View

        Description:
        - Send an approved template to an existing monitored conversation.

        Notes:
        - Company, number, conversation, WABA, and template are all resolved hierarchically.
        - A template belonging to another tenant or WABA cannot be supplied by changing template_id.
    """

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated, IsMember]

    def post(self, request, company_id, branch_id, number_id, conversation_id):
        whatsapp_number = resolve_monitoring_whatsapp_number(user=request.user, company_id=company_id, branch_id=branch_id, number_id=number_id)
        conversation = resolve_whatsapp_number_conversation(whatsapp_number=whatsapp_number, conversation_id=conversation_id)

        serializer = TemplateSendSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                {
                    "error_message": "Envío de plantilla rechazado: los datos proporcionados no son válidos.",
                    "data": serializer.errors,
                },
                status=HTTP_400_BAD_REQUEST,
            )

        template = resolve_waba_message_template(whatsapp_business_account=whatsapp_number.whatsapp_business_account, template_id=serializer.validated_data["template_id"])

        try:
            result = send_template_to_existing_conversation(
                conversation=conversation,
                whatsapp_message_template=template,
                send_components=serializer.validated_data["components"],
                actor=request.user,
            )

        except ValidationError as error:
            return Response(
                {
                    "error_message": "Envío de plantilla rechazado: no fue posible completar la operación.",
                    "data": error.message_dict,
                },
                status=HTTP_400_BAD_REQUEST,
            )

        except MetaWhatsAppAPIError as error:
            return Response(
                {
                    "error_message": "Envío de plantilla rechazado: Meta no aceptó el mensaje.",
                    "data": {
                        "meta": {
                            "http_status": error.http_status,
                            "error_code": error.meta_error_code,
                            "error_subcode": error.meta_error_subcode,
                            "message": error.error_message,
                        },
                    },
                },
                status=HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "success_message": "Plantilla aceptada por Meta; entrega pendiente de confirmación.",
                "data": {
                    "conversation_id": result["conversation"].id,
                    "message": MessageSerializer(result["message"]).data,
                    "meta": result["meta"],
                },
            },
            status=HTTP_201_CREATED,
        )


class NewConversationTemplateSendView(APIView):
    """
        DOCSTRING: New Conversation Template Send View

        Description:
        - Start or reuse a conversation by sending an approved template to a supplied WhatsApp recipient.

        Notes:
        - Company and source WhatsApp number are resolved before template resolution.
        - The template must belong to the exact WABA attached to the source number.
        - Recipient identity cannot influence tenant resolution.
    """

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated, IsMember]

    def post(self, request, company_id, branch_id, number_id):
        whatsapp_number = resolve_monitoring_whatsapp_number(user=request.user, company_id=company_id, branch_id=branch_id, number_id=number_id)

        serializer = NewConversationTemplateSendSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                {
                    "error_message": "Inicio de conversación rechazado: los datos proporcionados no son válidos.",
                    "data": serializer.errors,
                },
                status=HTTP_400_BAD_REQUEST,
            )

        template = resolve_waba_message_template(whatsapp_business_account=whatsapp_number.whatsapp_business_account, template_id=serializer.validated_data["template_id"])

        try:
            result = send_template_to_new_conversation(
                whatsapp_number=whatsapp_number,
                recipient_phone_number=serializer.validated_data["recipient_phone_number"],
                whatsapp_message_template=template,
                send_components=serializer.validated_data["components"],
                actor=request.user,
            )

        except ValidationError as error:
            return Response(
                {
                    "error_message": "Inicio de conversación rechazado: no fue posible completar la operación.",
                    "data": error.message_dict,
                },
                status=HTTP_400_BAD_REQUEST,
            )

        except MetaWhatsAppAPIError as error:
            return Response(
                {
                    "error_message": "Inicio de conversación rechazado: Meta no aceptó el mensaje.",
                    "data": {
                        "meta": {
                            "http_status": error.http_status,
                            "error_code": error.meta_error_code,
                            "error_subcode": error.meta_error_subcode,
                            "message": error.error_message,
                        },
                    },
                },
                status=HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "success_message": "Plantilla aceptada por Meta; entrega pendiente de confirmación.",
                "data": {
                    "customer_id": result["customer"].id,
                    "conversation_id": result["conversation"].id,
                    "message": MessageSerializer(result["message"]).data,
                    "meta": result["meta"],
                },
            },
            status=HTTP_201_CREATED,
        )
