import logging

from django.core.exceptions import (
    ImproperlyConfigured,
    ValidationError,
)

from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import (
    HTTP_200_OK,
    HTTP_400_BAD_REQUEST,
)
from rest_framework.views import APIView

from organizations.permissions import IsOrganizationAdministrator

from .embedded_signup_operations import (
    MetaEmbeddedSignupError,
    complete_whatsapp_embedded_signup,
)

from .integration_operations import (
    connect_whatsapp_business_account,
    disconnect_whatsapp_business_account,
    refresh_whatsapp_business_account_state,
    validate_whatsapp_number_connection,
)

from .meta_lifecycle import MetaLifecycleAPIError

from .resolvers import (
    resolve_administrative_company,
    resolve_company_branch,
    resolve_company_whatsapp_business_account,
    resolve_company_whatsapp_number,
)

from .serializers.integration import (
    WhatsAppBusinessAccountLifecycleResultSerializer,
    WhatsAppNumberValidationResultSerializer,
)

from .serializers.embedded_signup import (
    WhatsAppEmbeddedSignupRequestSerializer,
    WhatsAppEmbeddedSignupResultSerializer,
)


logger = logging.getLogger(__name__)


# Define your integration views here.


def build_lifecycle_error_response(error, error_message):
    """
        DOCSTRING: Build Lifecycle Error Response

        Description:
        - Convert Meta lifecycle and Embedded Signup exceptions
          into the standard Dialoqo API response structure.

        Notes:
        - Sensitive credentials are never returned.
        - Meta error classifications are preserved.
        - Validation errors are normalized.
        - Existing API response conventions remain unchanged.
    """

    if isinstance(error, ValidationError):

        response_data = (
            error.message_dict
            if hasattr(error, "message_dict")
            else {
                "validation": error.messages,
            }
        )

    elif isinstance(
        error,
        (
            MetaLifecycleAPIError,
            MetaEmbeddedSignupError,
        ),
    ):

        response_data = {
            "meta": {
                "http_status": error.http_status,
                "error_code": error.meta_error_code,
                "error_subcode": error.meta_error_subcode,
                "message": error.error_message,
            }
        }

    elif isinstance(error, ImproperlyConfigured):

        response_data = {
            "credentials": str(error),
        }

    else:

        response_data = {}

    return Response(
        {
            "error_message": error_message,
            "data": response_data,
        },
        status=HTTP_400_BAD_REQUEST,
    )


class WhatsAppEmbeddedSignupView(APIView):
    """
        DOCSTRING: WhatsApp Embedded Signup View

        Description:
        - Complete Meta WhatsApp Embedded Signup for a company.
        - Validate the Meta authorization information.
        - Register or reuse the authorized WABA.
        - Store the encrypted operational credential.
        - Automatically register the selected WhatsApp number.
        - Return the resulting onboarding information.

        Notes:
        - Only authenticated organization administrators
          may complete onboarding.
        - Company authorization is resolved before execution.
        - Meta authorization codes are exchanged by Django.
        - Access tokens are never returned to React.
        - Business Portfolio ownership is verified by Meta.
        - Newly registered numbers belong to Principal.
        - Monitoring remains disabled until explicitly activated.
        - Centralized auditing occurs in the operation layer.
    """

    authentication_classes = [
        TokenAuthentication,
    ]

    permission_classes = [
        IsAuthenticated,
        IsOrganizationAdministrator,
    ]

    input_serializer = WhatsAppEmbeddedSignupRequestSerializer

    output_serializer = WhatsAppEmbeddedSignupResultSerializer

    def post(self, request, company_id):
        """
            DOCSTRING: Complete Embedded Signup

            Description:
            - Validate the administrative company context.
            - Validate the Embedded Signup request.
            - Execute the onboarding operation.
            - Normalize and serialize the resulting resources.

            Notes:
            - The company is resolved from the URL.
            - The frontend cannot override company ownership.
            - Authorization codes and credentials are not logged.
        """

        # ---------------------------------------------------------
        # STEP 1: Resolve administrative company
        # ---------------------------------------------------------

        company = resolve_administrative_company(
            user=request.user,
            company_id=company_id,
        )

        # ---------------------------------------------------------
        # STEP 2: Validate request payload
        # ---------------------------------------------------------

        serializer = self.input_serializer(
            data=request.data
        )

        if not serializer.is_valid():

            return Response(
                {
                    "error_message": (
                        "Solicitud de Embedded Signup inválida."
                    ),
                    "data": serializer.errors,
                },
                status=HTTP_400_BAD_REQUEST,
            )

        validated_data = serializer.validated_data

        logger.info(
            "[EMBEDDED SIGNUP API] REQUEST RECEIVED | "
            "company_id=%s | actor_id=%s",
            company.id,
            request.user.id,
        )

        # ---------------------------------------------------------
        # STEP 3: Execute onboarding
        # ---------------------------------------------------------

        try:

            result = complete_whatsapp_embedded_signup(
                company=company,
                actor=request.user,
                authorization_code=validated_data["code"],
                meta_waba_id=validated_data["waba_id"],
                meta_phone_number_id=validated_data[
                    "phone_number_id"
                ],
                meta_business_id=validated_data.get(
                    "business_id"
                ),
            )

        except (
            ValidationError,
            MetaEmbeddedSignupError,
            MetaLifecycleAPIError,
            ImproperlyConfigured,
        ) as error:

            logger.warning(
                "[EMBEDDED SIGNUP API] ONBOARDING REJECTED | "
                "company_id=%s | actor_id=%s | "
                "error_type=%s",
                company.id,
                request.user.id,
                type(error).__name__,
            )

            return build_lifecycle_error_response(
                error=error,
                error_message=(
                    "No fue posible completar la conexión "
                    "de WhatsApp Business con Meta."
                ),
            )

        # ---------------------------------------------------------
        # STEP 4: Normalize onboarding response
        # ---------------------------------------------------------

        account = result["whatsapp_business_account"]

        response_data = {
            "account_id": account.id,
            "meta_waba_id": account.meta_waba_id,
            "meta_business_id": account.meta_business_id,
            "display_name": account.display_name,
            "created": result["created"],
            "is_connected": result["is_connected"],
            "is_webhook_configured": result[
                "is_webhook_configured"
            ],
            "credential_validated": result.get(
                "credential_validated",
                False,
            ),
            "credential_expires_at": result.get(
                "credential_expires_at"
            ),
            "selected_phone": result.get(
                "selected_phone",
                {}
            ),
            "phone_numbers": result.get(
                "phone_numbers",
                [],
            ),
            "registered_numbers": result.get(
                "registered_numbers",
                [],
            ),
            "created_numbers": result.get(
                "created_numbers",
                0,
            ),
            "reused_numbers": result.get(
                "reused_numbers",
                0,
            ),
        }

        # ---------------------------------------------------------
        # STEP 5: Serialize response
        # ---------------------------------------------------------

        output = self.output_serializer(
            response_data
        )

        logger.info(
            "[EMBEDDED SIGNUP API] ONBOARDING COMPLETED | "
            "company_id=%s | account_id=%s | "
            "created_numbers=%s | reused_numbers=%s",
            company.id,
            account.id,
            response_data["created_numbers"],
            response_data["reused_numbers"],
        )

        return Response(
            {
                "success_message": (
                    "WhatsApp Business conectado "
                    "correctamente mediante Meta."
                ),
                "data": output.data,
            },
            status=HTTP_200_OK,
        )


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
                    "Meta no pudo validar el Phone Number ID."
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