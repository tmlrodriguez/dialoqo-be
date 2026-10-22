"""Embedded Signup onboarding endpoints."""

import logging

from django.core.exceptions import ImproperlyConfigured, ValidationError
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_400_BAD_REQUEST
from rest_framework.views import APIView

from organizations.permissions import IsOrganizationAdministrator

from whatsapp.meta.embedded_signup import MetaEmbeddedSignupError
from whatsapp.meta.lifecycle import MetaLifecycleAPIError
from whatsapp.operations.embedded_signup import complete_whatsapp_embedded_signup
from whatsapp.serializers.embedded_signup import (
    EmbeddedSignupCompletionResultSerializer,
    EmbeddedSignupCompletionSerializer,
    WhatsAppEmbeddedSignupRequestSerializer,
    WhatsAppEmbeddedSignupResultSerializer,
)
from whatsapp.utils.resolvers import resolve_administrative_company
from whatsapp.utils.responses import (
    build_embedded_signup_error_response,
    build_lifecycle_error_response,
)


logger = logging.getLogger(__name__)


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


class WhatsAppEmbeddedSignupCompletionView(APIView):
    """
        DOCSTRING: WhatsApp Embedded Signup Completion View

        Description:
        - Receive the authorization result from Meta Embedded Signup.
        - Resolve the authenticated administrative company.
        - Validate the onboarding input.
        - Execute the backend Embedded Signup completion operation.
        - Return the connected WABA and discovered phone numbers.

        Notes:
        - Authentication uses the existing DRF token mechanism.
        - Only authorized organization administrators may execute
          the operation.
        - The company is resolved through the existing tenant
          authorization boundary.
        - The frontend cannot assign the WABA to another company.
        - Meta credentials remain exclusively on the backend.
        - Authorization codes are never logged.
        - Phone numbers are discovered but not automatically
          registered under a branch.
        - Monitoring activation remains a separate operation.
        - Centralized auditing is handled by the operation layer.
    """

    authentication_classes = [
        TokenAuthentication,
    ]

    permission_classes = [
        IsAuthenticated,
        IsOrganizationAdministrator,
    ]

    input_serializer = EmbeddedSignupCompletionSerializer

    output_serializer = EmbeddedSignupCompletionResultSerializer

    def post(self, request, company_id):
        """
            DOCSTRING: Complete Embedded Signup

            Description:
            - Validate the administrative company.
            - Validate Meta Embedded Signup authorization data.
            - Execute the onboarding workflow.
            - Return the resulting WABA connection state.
        """

        # ---------------------------------------------------------
        # STEP 1: Resolve administrative company
        # ---------------------------------------------------------

        company = resolve_administrative_company(
            user=request.user,
            company_id=company_id,
        )

        logger.info(
            "[EMBEDDED SIGNUP API] REQUEST RECEIVED | "
            "company_id=%s | actor_id=%s",
            company.id,
            request.user.id,
        )

        # ---------------------------------------------------------
        # STEP 2: Validate request data
        # ---------------------------------------------------------

        serializer = self.input_serializer(
            data=request.data,
        )

        if not serializer.is_valid():

            logger.warning(
                "[EMBEDDED SIGNUP API] VALIDATION FAILED | "
                "company_id=%s | actor_id=%s | fields=%s",
                company.id,
                request.user.id,
                list(serializer.errors.keys()),
            )

            return Response(
                {
                    "error_message":
                        "Embedded Signup rechazado: "
                        "los datos proporcionados no son válidos.",
                    "data": serializer.errors,
                },
                status=HTTP_400_BAD_REQUEST,
            )

        validated_data = serializer.validated_data

        # ---------------------------------------------------------
        # STEP 3: Log safe onboarding identifiers
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP API] INPUT VALIDATED | "
            "company_id=%s | "
            "waba_id=%s | "
            "phone_number_id=%s | "
            "business_id=%s | "
            "authorization_code_received=True",
            company.id,
            validated_data["waba_id"],
            validated_data["phone_number_id"],
            validated_data["business_id"],
        )

        # ---------------------------------------------------------
        # STEP 4: Complete onboarding
        # ---------------------------------------------------------

        try:

            result = complete_whatsapp_embedded_signup(
                company=company,
                actor=request.user,
                authorization_code=validated_data["code"],
                meta_waba_id=validated_data["waba_id"],
                meta_phone_number_id=validated_data["phone_number_id"],
                meta_business_id=validated_data["business_id"],
            )

        except (
            ValidationError,
            MetaEmbeddedSignupError,
            MetaLifecycleAPIError,
            ImproperlyConfigured,
        ) as error:

            logger.warning(
                "[EMBEDDED SIGNUP API] ONBOARDING REJECTED | "
                "company_id=%s | "
                "actor_id=%s | "
                "error_type=%s",
                company.id,
                request.user.id,
                type(error).__name__,
            )

            return build_embedded_signup_error_response(
                error=error,
                error_message=(
                    "Embedded Signup rechazado: "
                    "no fue posible completar la conexión con Meta."
                ),
            )

        # ---------------------------------------------------------
        # STEP 5: Prepare response
        # ---------------------------------------------------------

        account = result["whatsapp_business_account"]

        response_data = {
            "account_id": account.id,
            "company_id": company.id,
            "meta_waba_id": account.meta_waba_id,
            "meta_business_id": account.meta_business_id,
            "display_name": account.display_name,
            "created": result["created"],
            "is_connected": account.is_connected,
            "is_webhook_configured": account.is_webhook_configured,
            "phone_numbers": result["phone_numbers"],
        }

        # ---------------------------------------------------------
        # STEP 6: Serialize result
        # ---------------------------------------------------------

        output_serializer = self.output_serializer(
            response_data,
        )

        # ---------------------------------------------------------
        # STEP 7: Log successful onboarding
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP API] ONBOARDING COMPLETED | "
            "company_id=%s | "
            "actor_id=%s | "
            "account_id=%s | "
            "waba_id=%s | "
            "created=%s | "
            "connected=%s | "
            "webhook=%s | "
            "discovered_numbers=%s",
            company.id,
            request.user.id,
            account.id,
            account.meta_waba_id,
            result["created"],
            account.is_connected,
            account.is_webhook_configured,
            len(result["phone_numbers"]),
        )

        # ---------------------------------------------------------
        # STEP 8: Return successful response
        # ---------------------------------------------------------

        return Response(
            {
                "success_message":
                    "WhatsApp Business conectado correctamente "
                    "mediante Meta Embedded Signup.",
                "data": output_serializer.data,
            },
            status=HTTP_200_OK,
        )
