"""Shared Meta Graph API transport, credentials, and error handling."""

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from whatsapp.meta.auth.credentials import (
    get_meta_access_token as resolve_meta_access_token,
)


class MetaWhatsAppAPIError(Exception):
    """
        DOCSTRING: Meta WhatsApp API Error

        Description:
        - Represent a controlled failure returned while communicating
          with the Meta WhatsApp Cloud API.
        - Preserve safe diagnostic information required by the
          Dialoqo operation layer.

        Notes:
        - Access tokens and sensitive credentials must never appear
          in the exception.
        - http_status represents the HTTP response received from Meta.
        - meta_error_code represents Meta's normalized error code.
        - meta_error_subcode represents Meta's normalized error subcode.
    """

    def __init__(
        self,
        error_message,
        http_status=None,
        meta_error_code="",
        meta_error_subcode="",
    ):
        self.error_message = error_message
        self.http_status = http_status
        self.meta_error_code = str(meta_error_code or "")
        self.meta_error_subcode = str(meta_error_subcode or "")

        super().__init__(error_message)


def get_meta_graph_api_version():
    """
        DOCSTRING: Get Meta Graph API Version

        Description:
        - Resolve the Meta Graph API version used by Dialoqo.

        Notes:
        - The version is centralized through Django settings.
    """

    return getattr(
        settings,
        "DIALOQO_META_GRAPH_API_VERSION",
        getattr(
            settings,
            "Dialoqo_META_GRAPH_API_VERSION",
            "v26.0",
        ),
    )


def build_meta_graph_api_url(resource_path):
    """
        DOCSTRING: Build Meta Graph API URL

        Description:
        - Build a complete versioned Meta Graph API URL.

        Notes:
        - resource_path contains only the resource portion.
        - Credentials are never included in the URL.
    """

    graph_api_version = get_meta_graph_api_version()
    normalized_resource_path = str(resource_path).lstrip("/")

    return (
        f"https://graph.facebook.com/"
        f"{graph_api_version}/"
        f"{normalized_resource_path}"
    )


def extract_meta_api_error(response):
    """
        DOCSTRING: Extract Meta API Error

        Description:
        - Normalize an unsuccessful Meta API response.

        Notes:
        - Invalid non-JSON responses are handled safely.
        - Access tokens are never included in the error.
        - Meta error classifications are preserved.
    """

    try:
        response_data = response.json()

    except ValueError:
        response_data = {}

    if not isinstance(response_data, dict):
        response_data = {}

    error_data = response_data.get("error") or {}

    if not isinstance(error_data, dict):
        error_data = {}

    return MetaWhatsAppAPIError(
        error_message=(
            error_data.get("message")
            or "Meta rechazó la solicitud de WhatsApp."
        ),
        http_status=response.status_code,
        meta_error_code=error_data.get("code") or "",
        meta_error_subcode=error_data.get("error_subcode") or "",
    )


def get_meta_access_token(whatsapp_business_account=None):
    """
        DOCSTRING: Get Meta Access Token

        Description:
        - Resolve the authorized Meta access token.
        - Delegate WABA credential resolution to the authentication layer.
        - Preserve the specific reason for local configuration failures.

        Notes:
        - WABA requests require their own stored operational credential.
        - Calls without a WABA retain platform-level credential resolution.
        - Configuration failures are normalized.
        - Credential configuration errors contain no token values.
    """

    try:
        return resolve_meta_access_token(
            whatsapp_business_account=whatsapp_business_account,
        )

    except ImproperlyConfigured as error:
        raise MetaWhatsAppAPIError(
            error_message=str(error),
        ) from error


def meta_get(
    resource_path,
    params=None,
    whatsapp_business_account=None,
):
    """
        DOCSTRING: Meta Get

        Description:
        - Execute an authenticated GET request against Meta Graph API.
        - Resolve the access token using the supplied WABA context.

        Notes:
        - Existing calls without a WABA remain compatible.
        - Network and API failures are normalized.
        - Credentials are never exposed.
    """

    access_token = get_meta_access_token(
        whatsapp_business_account=whatsapp_business_account,
    )

    try:
        response = requests.get(
            build_meta_graph_api_url(resource_path),
            headers={
                "Authorization": f"Bearer {access_token}",
            },
            params=params or {},
            timeout=getattr(
                settings,
                "DIALOQO_META_REQUEST_TIMEOUT",
                20,
            ),
        )

    except requests.RequestException as error:
        raise MetaWhatsAppAPIError(
            error_message=(
                "Operación de Meta rechazada: "
                "no fue posible comunicarse con Meta."
            )
        ) from error

    if not response.ok:
        raise extract_meta_api_error(response)

    try:
        return response.json()

    except ValueError as error:
        raise MetaWhatsAppAPIError(
            error_message=(
                "Operación de Meta rechazada: "
                "Meta devolvió una respuesta inválida."
            ),
            http_status=response.status_code,
        ) from error


def meta_post(
    resource_path,
    payload=None,
    params=None,
    whatsapp_business_account=None,
):
    """
        DOCSTRING: Meta Post

        Description:
        - Execute an authenticated POST request against Meta Graph API.
        - Resolve the access token using the supplied WABA context.

        Notes:
        - Existing calls without a WABA remain compatible.
        - Network and API failures are normalized.
        - Credentials are never exposed.
    """

    access_token = get_meta_access_token(
        whatsapp_business_account=whatsapp_business_account,
    )

    try:
        response = requests.post(
            build_meta_graph_api_url(resource_path),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            params=params or {},
            json=payload if payload is not None else {},
            timeout=getattr(
                settings,
                "DIALOQO_META_REQUEST_TIMEOUT",
                20,
            ),
        )

    except requests.RequestException as error:
        raise MetaWhatsAppAPIError(
            error_message=(
                "Operación de Meta rechazada: "
                "no fue posible comunicarse con Meta."
            )
        ) from error

    if not response.ok:
        raise extract_meta_api_error(response)

    try:
        return response.json()

    except ValueError as error:
        raise MetaWhatsAppAPIError(
            error_message=(
                "Operación de Meta rechazada: "
                "Meta devolvió una respuesta inválida."
            ),
            http_status=response.status_code,
        ) from error


def meta_delete(
    resource_path,
    params=None,
    whatsapp_business_account=None,
):
    """
        DOCSTRING: Meta Delete

        Description:
        - Execute an authenticated DELETE request against Meta Graph API.
        - Resolve the access token using the supplied WABA context.

        Notes:
        - Existing calls without a WABA remain compatible.
        - Successful empty responses are supported.
        - Network and API failures are normalized.
    """

    access_token = get_meta_access_token(
        whatsapp_business_account=whatsapp_business_account,
    )

    try:
        response = requests.delete(
            build_meta_graph_api_url(resource_path),
            headers={
                "Authorization": f"Bearer {access_token}",
            },
            params=params or {},
            timeout=getattr(
                settings,
                "DIALOQO_META_REQUEST_TIMEOUT",
                20,
            ),
        )

    except requests.RequestException as error:
        raise MetaWhatsAppAPIError(
            error_message=(
                "Operación de Meta rechazada: "
                "no fue posible comunicarse con Meta."
            )
        ) from error

    if not response.ok:
        raise extract_meta_api_error(response)

    try:
        return response.json()

    except ValueError:
        return {
            "success": True,
        }
