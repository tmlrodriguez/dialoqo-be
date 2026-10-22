import logging

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from whatsapp.meta.auth.credentials import get_meta_access_token


logger = logging.getLogger(__name__)


# Define your Meta lifecycle operations here.


class MetaLifecycleAPIError(Exception):
    """
        DOCSTRING: Meta Lifecycle API Error

        Description:
        - Represent a controlled Meta Graph API failure occurring
          during WhatsApp lifecycle operations.

        Notes:
        - Sensitive access tokens are never included.
        - HTTP status and Meta error identifiers are preserved.
        - Raw Meta responses are never exposed.
    """

    def __init__(
        self,
        message,
        http_status=None,
        meta_error_code=None,
        meta_error_subcode=None,
    ):
        self.error_message = message
        self.http_status = http_status
        self.meta_error_code = meta_error_code
        self.meta_error_subcode = meta_error_subcode

        super().__init__(message)


class MetaLifecycleClient:
    """
        DOCSTRING: Meta Lifecycle Client

        Description:
        - Provide Meta Graph API operations required to validate
          and synchronize WhatsApp Business resources.
        - Resolve authorization through the centralized
          Dialoqo credential resolver.

        Notes:
        - Account operations use the corresponding WABA credential.
        - Calls without a WABA use the configured global token for
          platform operations and explicit manual onboarding.
        - Access tokens are never persisted by this client.
        - Graph API version is configured centrally.
        - Messaging and template operations remain separate.
    """

    def __init__(
        self,
        whatsapp_business_account=None,
    ):
        """
            DOCSTRING: Initialize Meta Lifecycle Client

            Description:
            - Initialize the Meta lifecycle client.
            - Resolve the appropriate operational credential.

            Notes:
            - WABA context is optional for backward compatibility.
            - Existing calls to MetaLifecycleClient() remain valid.
        """

        self.whatsapp_business_account = (
            whatsapp_business_account
        )

        self.access_token = get_meta_access_token(
            whatsapp_business_account=whatsapp_business_account,
        )

        if not self.access_token:
            raise ImproperlyConfigured(
                "Configuración de Meta rechazada: "
                "el access token no se encuentra configurado."
            )

        self.graph_api_version = getattr(
            settings,
            "DIALOQO_META_GRAPH_API_VERSION",
            getattr(
                settings,
                "Dialoqo_META_GRAPH_API_VERSION",
                "v26.0",
            ),
        )

        self.base_url = (
            f"https://graph.facebook.com/"
            f"{self.graph_api_version}"
        )

        self.request_timeout = getattr(
            settings,
            "DIALOQO_META_REQUEST_TIMEOUT",
            20,
        )

    def _request(
        self,
        method,
        resource_path,
        params=None,
        json_payload=None,
    ):
        """
            DOCSTRING: Request

            Description:
            - Execute an authenticated Meta Graph API request.
            - Normalize network failures and Meta API errors.

            Notes:
            - Authorization uses the Bearer header.
            - Credentials are never included in URLs or logs.
            - Empty successful responses are normalized.
            - Only controlled error information is exposed.
        """

        resource_path = str(resource_path).lstrip("/")

        url = (
            f"{self.base_url}/"
            f"{resource_path}"
        )

        try:
            response = requests.request(
                method=method,
                url=url,
                headers={
                    "Authorization": (
                        f"Bearer {self.access_token}"
                    ),
                    "Content-Type": "application/json",
                },
                params=params,
                json=json_payload,
                timeout=self.request_timeout,
            )

        except requests.RequestException as error:
            raise MetaLifecycleAPIError(
                "Meta no pudo ser contactado durante "
                "la validación de la integración."
            ) from error

        try:
            response_data = (
                response.json()
                if response.content
                else {}
            )

        except ValueError as error:
            raise MetaLifecycleAPIError(
                "Meta devolvió una respuesta "
                "que no contiene JSON válido.",
                http_status=response.status_code,
            ) from error

        if response.ok:
            return response_data

        error_data = (
            response_data.get("error")
            if isinstance(response_data, dict)
            else {}
        )

        if not isinstance(error_data, dict):
            error_data = {}

        raise MetaLifecycleAPIError(
            error_data.get("message")
            or "Meta rechazó la operación de ciclo de vida.",
            http_status=response.status_code,
            meta_error_code=error_data.get("code"),
            meta_error_subcode=error_data.get("error_subcode"),
        )

    def validate_credentials(self):
        """
            DOCSTRING: Validate Credentials

            Description:
            - Validate the currently resolved Meta access token.

            Notes:
            - Successful validation proves token acceptance.
            - It does not prove access to a particular WABA.
        """

        return self._request(
            method="GET",
            resource_path="me",
            params={
                "fields": "id,name",
            },
        )

    def get_waba(
        self,
        meta_waba_id,
    ):
        """
            DOCSTRING: Get WABA

            Description:
            - Retrieve and validate access to a Meta WABA.
            - Return the current WABA representation.
        """

        return self._request(
            method="GET",
            resource_path=meta_waba_id,
            params={
                "fields": "id,name",
            },
        )

    def list_waba_phone_numbers(
        self,
        meta_waba_id,
    ):
        """
            DOCSTRING: List WABA Phone Numbers

            Description:
            - Retrieve phone numbers associated with a WABA.
            - Support phone-number membership validation.

            Notes:
            - Phone Number IDs are authoritative Meta identifiers.
            - The response is normalized to a list.
            - This preserves the existing first-page behavior.
        """

        response_data = self._request(
            method="GET",
            resource_path=(
                f"{meta_waba_id}/phone_numbers"
            ),
            params={
                "fields": (
                    "id,"
                    "display_phone_number,"
                    "verified_name,"
                    "code_verification_status"
                ),
                "limit": 100,
            },
        )

        return response_data.get("data") or []

    def get_phone_number(
        self,
        meta_phone_number_id,
    ):
        """
            DOCSTRING: Get Phone Number

            Description:
            - Retrieve a specific Meta WhatsApp phone number.
            - Validate its identity and current Meta information.
        """

        return self._request(
            method="GET",
            resource_path=meta_phone_number_id,
            params={
                "fields": (
                    "id,"
                    "display_phone_number,"
                    "verified_name,"
                    "code_verification_status"
                ),
            },
        )

    def list_subscribed_apps(
        self,
        meta_waba_id,
    ):
        """
            DOCSTRING: List Subscribed Apps

            Description:
            - Retrieve applications currently subscribed
              to receive webhook events for a WABA.
        """

        response_data = self._request(
            method="GET",
            resource_path=(
                f"{meta_waba_id}/subscribed_apps"
            ),
        )

        return response_data.get("data") or []

    def subscribe_waba(
        self,
        meta_waba_id,
    ):
        """
            DOCSTRING: Subscribe WABA

            Description:
            - Subscribe Dialoqo's Meta application to
              webhook events for the specified WABA.

            Notes:
            - Meta determines whether the token has
              sufficient authorization.
            - Subscription verification remains the
              responsibility of the operation layer.
        """

        return self._request(
            method="POST",
            resource_path=(
                f"{meta_waba_id}/subscribed_apps"
            ),
        )

    def unsubscribe_waba(
        self,
        meta_waba_id,
    ):
        """
            DOCSTRING: Unsubscribe WABA

            Description:
            - Remove Dialoqo's application subscription
              from the specified WABA.

            Notes:
            - This operation modifies external Meta configuration.
            - Local connection state must be synchronized separately.
        """

        return self._request(
            method="DELETE",
            resource_path=(
                f"{meta_waba_id}/subscribed_apps"
            ),
        )


def payload_contains_meta_id(
    payload,
    expected_id,
):
    """
        DOCSTRING: Payload Contains Meta ID

        Description:
        - Determine whether a Meta response contains
          a particular object identifier.

        Notes:
        - Nested dictionaries and lists are supported.
        - Only exact string identifier matches are accepted.
        - Existing behavior is preserved.
    """

    expected_id = str(expected_id)

    if isinstance(payload, dict):

        for key, value in payload.items():

            if (
                key == "id"
                and str(value) == expected_id
            ):
                return True

            if payload_contains_meta_id(
                value,
                expected_id,
            ):
                return True

        return False

    if isinstance(payload, list):

        return any(
            payload_contains_meta_id(
                value,
                expected_id,
            )
            for value in payload
        )

    return False


def log_meta_lifecycle_error(operation, error, **context):
    """
        DOCSTRING: Log Meta Lifecycle Error

        Description:
        - Generate structured diagnostic logs for Meta lifecycle failures.
        - Preserve relevant resource identifiers and error classifications.

        Notes:
        - Access tokens and application secrets are never logged.
        - Raw Meta responses are never logged.
        - Customer phone numbers and message contents are excluded.
        - Meta error codes are retained for troubleshooting.
        - Local credential configuration messages contain no secret values.
    """

    diagnostic_data = {
        "operation": operation,
        "error_type": type(error).__name__,
        **context,
    }

    if isinstance(error, MetaLifecycleAPIError):
        diagnostic_data.update({
            "http_status": error.http_status,
            "meta_error_code": error.meta_error_code,
            "meta_error_subcode": error.meta_error_subcode,
        })
    elif isinstance(error, ImproperlyConfigured):
        diagnostic_data["configuration_error"] = str(error)

    logger.error(
        "[META LIFECYCLE] ERROR | %s",
        diagnostic_data,
    )
