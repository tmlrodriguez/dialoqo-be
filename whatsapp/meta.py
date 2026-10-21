import requests

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .credentials import (
    get_meta_access_token as resolve_meta_access_token,
)
from .webhook import normalize_phone_number


# Define your Meta API helpers here.


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
        - Delegate credential resolution to credentials.py.
        - Support the current global Meta configuration.
        - Prepare Meta operations for WABA-specific authorization.

        Notes:
        - Existing integrations continue using the global token.
        - Customer-specific credentials can be introduced later.
        - The WABA context is optional for backward compatibility.
        - Configuration failures are normalized.
        - Access tokens are never logged.
    """

    try:
        return resolve_meta_access_token(
            whatsapp_business_account=whatsapp_business_account,
        )

    except ImproperlyConfigured as error:
        raise MetaWhatsAppAPIError(
            error_message=(
                "Operación de Meta rechazada: "
                "la integración no se encuentra correctamente configurada."
            )
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


def send_meta_whatsapp_text_message(
    whatsapp_number,
    recipient_phone_number,
    text_body,
):
    """
        DOCSTRING: Send Meta WhatsApp Text Message

        Description:
        - Send a plain-text WhatsApp message through Meta Cloud API.
        - Resolve authorization through the owning WABA.

        Notes:
        - Business validation and persistence belong to the
          operation layer.
        - Recipient phone numbers are normalized.
        - Meta provides the authoritative message identifier.
    """

    normalized_recipient = normalize_phone_number(
        recipient_phone_number
    )

    if not normalized_recipient:
        raise MetaWhatsAppAPIError(
            error_message=(
                "Envío de mensaje rechazado: "
                "el número de destino no es válido."
            )
        )

    response_data = meta_post(
        resource_path=(
            f"{whatsapp_number.meta_phone_number_id}/messages"
        ),
        whatsapp_business_account=(
            whatsapp_number.whatsapp_business_account
        ),
        payload={
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalized_recipient,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": text_body,
            },
        },
    )

    messages = response_data.get("messages") or []

    if not messages or not messages[0].get("id"):
        raise MetaWhatsAppAPIError(
            error_message=(
                "Envío de mensaje rechazado: "
                "Meta no devolvió un identificador "
                "de mensaje válido."
            )
        )

    return {
        "meta_message_id": messages[0]["id"],
        "recipient_phone_number": normalized_recipient,
        "response_data": response_data,
    }


def list_meta_whatsapp_message_templates(
    whatsapp_business_account,
):
    """
        DOCSTRING: List Meta WhatsApp Message Templates

        Description:
        - Retrieve all message templates belonging to a WABA.
        - Follow Meta pagination until all templates are retrieved.
        - Resolve authorization through the owning WABA.

        Notes:
        - Template normalization and persistence belong to
          the synchronization operation.
        - Pagination cursors are used instead of requesting
          arbitrary external URLs.
    """

    templates = []
    after = None

    while True:
        params = {
            "limit": 100,
        }

        if after:
            params["after"] = after

        response_data = meta_get(
            resource_path=(
                f"{whatsapp_business_account.meta_waba_id}"
                "/message_templates"
            ),
            params=params,
            whatsapp_business_account=whatsapp_business_account,
        )

        templates.extend(
            response_data.get("data") or []
        )

        paging = response_data.get("paging") or {}
        cursors = paging.get("cursors") or {}

        next_url = paging.get("next")
        after = cursors.get("after")

        if not next_url or not after:
            break

    return templates


def create_meta_whatsapp_message_template(
    whatsapp_business_account,
    name,
    language,
    category,
    parameter_format,
    components,
):
    """
        DOCSTRING: Create Meta WhatsApp Message Template

        Description:
        - Submit a new WhatsApp message template to Meta.
        - Resolve authorization through the owning WABA.

        Notes:
        - Meta performs final template validation and approval.
        - Newly created templates may remain pending.
    """

    payload = {
        "name": name,
        "language": language,
        "category": category,
        "components": components,
    }

    if parameter_format:
        payload["parameter_format"] = parameter_format

    return meta_post(
        resource_path=(
            f"{whatsapp_business_account.meta_waba_id}"
            "/message_templates"
        ),
        payload=payload,
        whatsapp_business_account=whatsapp_business_account,
    )


def update_meta_whatsapp_message_template(
    whatsapp_message_template,
    category=None,
    components=None,
):
    """
        DOCSTRING: Update Meta WhatsApp Message Template

        Description:
        - Submit supported message-template modifications to Meta.
        - Resolve authorization through the owning WABA.

        Notes:
        - Meta determines whether a template permits modification.
        - Local state must not assume success until Meta accepts it.
    """

    payload = {}

    if category is not None:
        payload["category"] = category

    if components is not None:
        payload["components"] = components

    if not payload:
        raise MetaWhatsAppAPIError(
            error_message=(
                "Actualización de plantilla rechazada: "
                "no se proporcionaron cambios."
            )
        )

    return meta_post(
        resource_path=(
            whatsapp_message_template.meta_template_id
        ),
        payload=payload,
        whatsapp_business_account=(
            whatsapp_message_template.whatsapp_business_account
        ),
    )


def delete_meta_whatsapp_message_template(
    whatsapp_message_template,
):
    """
        DOCSTRING: Delete Meta WhatsApp Message Template

        Description:
        - Request deletion of a WhatsApp message template from Meta.
        - Resolve authorization through the owning WABA.

        Notes:
        - Deletion is addressed through the owning WABA
          and template name.
        - Local historical records remain preserved.
    """

    whatsapp_business_account = (
        whatsapp_message_template.whatsapp_business_account
    )

    return meta_delete(
        resource_path=(
            f"{whatsapp_business_account.meta_waba_id}"
            "/message_templates"
        ),
        params={
            "name": whatsapp_message_template.name,
        },
        whatsapp_business_account=whatsapp_business_account,
    )


def send_meta_whatsapp_template_message(
    whatsapp_number,
    recipient_phone_number,
    template_name,
    language,
    components=None,
):
    """
        DOCSTRING: Send Meta WhatsApp Template Message

        Description:
        - Send an approved WhatsApp template message through
          Meta WhatsApp Cloud API.
        - Resolve authorization through the owning WABA.

        Notes:
        - Recipient phone numbers are normalized.
        - Template parameters use Meta component format.
        - Meta returns the authoritative message identifier.
    """

    normalized_recipient = normalize_phone_number(
        recipient_phone_number
    )

    if not normalized_recipient:
        raise MetaWhatsAppAPIError(
            error_message=(
                "Envío de plantilla rechazado: "
                "el número de destino no es válido."
            )
        )

    template_payload = {
        "name": template_name,
        "language": {
            "code": language,
        },
    }

    if components:
        template_payload["components"] = components

    response_data = meta_post(
        resource_path=(
            f"{whatsapp_number.meta_phone_number_id}/messages"
        ),
        whatsapp_business_account=(
            whatsapp_number.whatsapp_business_account
        ),
        payload={
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalized_recipient,
            "type": "template",
            "template": template_payload,
        },
    )

    messages = response_data.get("messages") or []

    if not messages or not messages[0].get("id"):
        raise MetaWhatsAppAPIError(
            error_message=(
                "Envío de plantilla rechazado: "
                "Meta no devolvió un identificador "
                "de mensaje válido."
            )
        )

    return {
        "meta_message_id": messages[0]["id"],
        "recipient_phone_number": normalized_recipient,
        "response_data": response_data,
    }