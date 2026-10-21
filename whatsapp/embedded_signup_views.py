import logging

from django.core.exceptions import (
    ImproperlyConfigured,
    ValidationError,
)

from rest_framework import serializers
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import (
    HTTP_200_OK,
    HTTP_400_BAD_REQUEST,
)
from rest_framework.views import APIView

from organizations.permissions import (
    IsOrganizationAdministrator,
)

from .embedded_signup_operations import (
    MetaEmbeddedSignupError,
    complete_whatsapp_embedded_signup,
)
from .meta_lifecycle import MetaLifecycleAPIError
from .resolvers import resolve_administrative_company


logger = logging.getLogger(__name__)


# Define your Embedded Signup API views here.


class EmbeddedSignupCompletionSerializer(serializers.Serializer):
    """
        DOCSTRING: Embedded Signup Completion Serializer

        Description:
        - Validate the information returned by Meta Embedded Signup.
        - Normalize the authorization code and Meta resource identifiers.
        - Provide validated input to the onboarding operation.

        Notes:
        - The authorization code is required.
        - The WABA ID is required.
        - The Phone Number ID is required.
        - The Meta Business Portfolio ID is required by the
          current onboarding operation.
        - Credentials are never returned in API responses.
        - Company ownership is determined from the authenticated
          administrative URL context.
    """

    code = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
        max_length=4096,
        write_only=True,
    )

    waba_id = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
        max_length=100,
    )

    phone_number_id = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
        max_length=100,
    )

    business_id = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
        max_length=100,
    )

    def validate(self, attrs):
        """
            DOCSTRING: Validate Embedded Signup Completion

            Description:
            - Validate the structure of Meta resource identifiers.
            - Reject malformed onboarding requests.

            Notes:
            - This validation does not establish Meta ownership.
            - Actual authorization is verified by backend operations.
        """

        identifier_fields = [
            "waba_id",
            "phone_number_id",
            "business_id",
        ]

        for field in identifier_fields:

            value = attrs.get(field)

            if not value or not value.isdigit():
                raise serializers.ValidationError({
                    field:
                        "Debe proporcionar un identificador "
                        "numérico válido de Meta."
                })

        return attrs


class EmbeddedSignupPhoneNumberSerializer(serializers.Serializer):
    """
        DOCSTRING: Embedded Signup Phone Number Serializer

        Description:
        - Represent one phone number discovered during onboarding.

        Notes:
        - Phone numbers are returned for subsequent configuration.
        - Numbers are not automatically assigned to branches.
        - Monitoring is not automatically activated.
    """

    meta_phone_number_id = serializers.CharField()

    display_phone_number = serializers.CharField(
        allow_blank=True,
    )

    verified_name = serializers.CharField(
        allow_blank=True,
    )

    code_verification_status = serializers.CharField(
        allow_blank=True,
        required=False,
    )

    is_selected = serializers.BooleanField(
        required=False,
    )


class EmbeddedSignupCompletionResultSerializer(serializers.Serializer):
    """
        DOCSTRING: Embedded Signup Completion Result Serializer

        Description:
        - Serialize the result of a completed Embedded Signup operation.
        - Expose the registered WABA and discovered phone numbers.

        Notes:
        - Access tokens are never exposed.
        - Authorization codes are never exposed.
        - Meta App Secret is never exposed.
        - The response is limited to operational onboarding data.
    """

    account_id = serializers.IntegerField()

    company_id = serializers.IntegerField()

    meta_waba_id = serializers.CharField()

    meta_business_id = serializers.CharField()

    display_name = serializers.CharField()

    created = serializers.BooleanField()

    is_connected = serializers.BooleanField()

    is_webhook_configured = serializers.BooleanField()

    phone_numbers = EmbeddedSignupPhoneNumberSerializer(
        many=True,
    )


def build_embedded_signup_error_response(
    error,
    error_message,
):
    """
        DOCSTRING: Build Embedded Signup Error Response

        Description:
        - Normalize Embedded Signup failures into the standard
          Dialoqo API response structure.

        Notes:
        - Authorization codes and access tokens are never included.
        - Meta error classifications are preserved.
        - Sensitive Meta responses are never returned.
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
            MetaEmbeddedSignupError,
            MetaLifecycleAPIError,
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
            "configuration":
                "La configuración de Meta no se encuentra disponible."
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