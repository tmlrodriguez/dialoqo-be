"""Business account connection and phone number validation endpoints."""

from django.core.exceptions import ImproperlyConfigured, ValidationError
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK
from rest_framework.views import APIView

from organizations.permissions import IsOrganizationAdministrator

from whatsapp.meta.lifecycle import MetaLifecycleAPIError
from whatsapp.operations.integration.accounts import (
    connect_whatsapp_business_account,
    disconnect_whatsapp_business_account,
    refresh_whatsapp_business_account_state,
)
from whatsapp.operations.integration.numbers import validate_whatsapp_number_connection
from whatsapp.serializers.integration import (
    WhatsAppBusinessAccountLifecycleResultSerializer,
    WhatsAppNumberValidationResultSerializer,
)
from whatsapp.utils.resolvers import (
    resolve_administrative_company,
    resolve_company_branch,
    resolve_company_whatsapp_business_account,
    resolve_company_whatsapp_number,
)
from whatsapp.utils.responses import build_lifecycle_error_response


class WhatsAppBusinessAccountConnectionView(APIView):
    """
        DOCSTRING: WhatsApp Business Account Connection View

        Description:
        - Connect, refresh, and disconnect the actual
          Meta lifecycle of a WhatsApp Business Account.

        Notes:
        - POST validates the WABA and subscribes Dialoqo.
        - PATCH refreshes state without modifying subscription.
        - DELETE removes the application's Meta subscription.
        - Credentials are resolved by the backend.
        - Existing lifecycle API behavior is preserved.
    """

    authentication_classes = [
        TokenAuthentication,
    ]

    permission_classes = [
        IsAuthenticated,
        IsOrganizationAdministrator,
    ]

    output_serializer = (
        WhatsAppBusinessAccountLifecycleResultSerializer
    )

    def post(self, request, company_id, account_id):

        company = resolve_administrative_company(
            user=request.user,
            company_id=company_id,
        )

        account = resolve_company_whatsapp_business_account(
            company=company,
            account_id=account_id,
        )

        try:

            result = connect_whatsapp_business_account(
                whatsapp_business_account=account,
                actor=request.user,
            )

        except (
            ValidationError,
            MetaLifecycleAPIError,
            ImproperlyConfigured,
        ) as error:

            return build_lifecycle_error_response(
                error=error,
                error_message=(
                    "Conexión de cuenta rechazada: "
                    "no fue posible conectar la WABA con Meta."
                ),
            )

        return Response(
            {
                "success_message": (
                    "Cuenta de WhatsApp Business "
                    "conectada correctamente."
                ),
                "data": self.output_serializer(
                    result
                ).data,
            },
            status=HTTP_200_OK,
        )

    def patch(self, request, company_id, account_id):

        company = resolve_administrative_company(
            user=request.user,
            company_id=company_id,
        )

        account = resolve_company_whatsapp_business_account(
            company=company,
            account_id=account_id,
        )

        try:

            result = refresh_whatsapp_business_account_state(
                whatsapp_business_account=account,
                actor=request.user,
            )

        except (
            ValidationError,
            MetaLifecycleAPIError,
            ImproperlyConfigured,
        ) as error:

            return build_lifecycle_error_response(
                error=error,
                error_message=(
                    "Actualización de estado rechazada: "
                    "no fue posible verificar la WABA contra Meta."
                ),
            )

        return Response(
            {
                "success_message": (
                    "Estado de cuenta de WhatsApp Business "
                    "actualizado correctamente."
                ),
                "data": self.output_serializer(
                    result
                ).data,
            },
            status=HTTP_200_OK,
        )

    def delete(self, request, company_id, account_id):

        company = resolve_administrative_company(
            user=request.user,
            company_id=company_id,
        )

        account = resolve_company_whatsapp_business_account(
            company=company,
            account_id=account_id,
        )

        try:

            result = disconnect_whatsapp_business_account(
                whatsapp_business_account=account,
                actor=request.user,
            )

        except (
            ValidationError,
            MetaLifecycleAPIError,
            ImproperlyConfigured,
        ) as error:

            return build_lifecycle_error_response(
                error=error,
                error_message=(
                    "Desconexión de cuenta rechazada: "
                    "no fue posible eliminar la suscripción en Meta."
                ),
            )

        return Response(
            {
                "success_message": (
                    "Cuenta de WhatsApp Business "
                    "desconectada correctamente."
                ),
                "data": self.output_serializer(
                    result
                ).data,
            },
            status=HTTP_200_OK,
        )


class WhatsAppNumberValidationView(APIView):
    """
        DOCSTRING: WhatsApp Number Validation View

        Description:
        - Validate a configured Phone Number ID
          against its actual Meta WhatsApp Business Account.

        Notes:
        - Company and branch boundaries are enforced.
        - The backend synchronizes is_connected.
        - Existing validation behavior is preserved.
    """

    authentication_classes = [
        TokenAuthentication,
    ]

    permission_classes = [
        IsAuthenticated,
        IsOrganizationAdministrator,
    ]

    output_serializer = WhatsAppNumberValidationResultSerializer

    def post(
        self,
        request,
        company_id,
        branch_id,
        number_id,
    ):

        company = resolve_administrative_company(
            user=request.user,
            company_id=company_id,
        )

        branch = resolve_company_branch(
            company=company,
            branch_id=branch_id,
        )

        whatsapp_number = resolve_company_whatsapp_number(
            company=company,
            branch_id=branch.id,
            number_id=number_id,
        )

        try:

            result = validate_whatsapp_number_connection(
                whatsapp_number=whatsapp_number,
                actor=request.user,
            )

        except (
            ValidationError,
            MetaLifecycleAPIError,
            ImproperlyConfigured,
        ) as error:

            return build_lifecycle_error_response(
                error=error,
                error_message=(
                    "Validación de número rechazada: "
                    "no fue posible validar el Phone Number ID."
                ),
            )

        return Response(
            {
                "success_message": (
                    "Número de WhatsApp validado correctamente."
                ),
                "data": self.output_serializer(
                    result
                ).data,
            },
            status=HTTP_200_OK,
        )
