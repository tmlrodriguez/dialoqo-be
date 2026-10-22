"""API error responses for Meta lifecycle and onboarding failures."""

from django.core.exceptions import ImproperlyConfigured, ValidationError
from rest_framework.response import Response
from rest_framework.status import HTTP_400_BAD_REQUEST

from whatsapp.meta.embedded_signup import MetaEmbeddedSignupError
from whatsapp.meta.lifecycle import MetaLifecycleAPIError


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
