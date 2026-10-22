"""Meta Embedded Signup authorization and resource validation."""

import logging
from datetime import datetime, timezone as datetime_timezone

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from whatsapp.meta.auth.credentials import get_meta_app_id, get_meta_app_secret
from whatsapp.meta.lifecycle import payload_contains_meta_id


logger = logging.getLogger(__name__)


REQUIRED_META_PERMISSIONS = {
    "whatsapp_business_management",
    "whatsapp_business_messaging",
}


class MetaEmbeddedSignupError(Exception):
    """
        DOCSTRING: Meta Embedded Signup Error

        Description:
        - Represent controlled failures during Embedded Signup.

        Notes:
        - Authorization codes and access tokens are never exposed.
        - HTTP status and Meta error classifications are preserved.
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


def get_embedded_signup_graph_url(resource_path):
    """
        DOCSTRING: Get Embedded Signup Graph URL

        Description:
        - Construct a versioned Meta Graph API URL.

        Notes:
        - Graph API version is configured centrally.
        - Credentials are never included in generated URLs.
    """

    version = getattr(
        settings,
        "DIALOQO_META_GRAPH_API_VERSION",
        "v26.0",
    )

    return (
        f"https://graph.facebook.com/"
        f"{version}/"
        f"{str(resource_path).lstrip('/')}"
    )


def exchange_embedded_signup_authorization_code(code):
    """
        DOCSTRING: Exchange Embedded Signup Authorization Code

        Description:
        - Exchange an authorization code for a Meta access token.

        Notes:
        - Executed exclusively in Django.
        - The App Secret never reaches React.
        - Authorization codes and tokens are never logged.
    """

    if not isinstance(code, str) or not code.strip():
        raise ValidationError({
            "code": "El código de autorización es obligatorio."
        })

    logger.info(
        "[EMBEDDED SIGNUP] AUTHORIZATION EXCHANGE START"
    )

    app_secret = get_meta_app_secret()

    try:
        response = requests.get(
            get_embedded_signup_graph_url(
                "oauth/access_token"
            ),
            params={
                "client_id": get_meta_app_id(),
                "client_secret": app_secret,
                "code": code,
            },
            timeout=getattr(
                settings,
                "DIALOQO_META_REQUEST_TIMEOUT",
                30,
            ),
        )

    except requests.RequestException as error:
        raise MetaEmbeddedSignupError(
            "No fue posible contactar a Meta."
        ) from error

    try:
        response_data = response.json()

    except ValueError as error:
        raise MetaEmbeddedSignupError(
            "Meta devolvió una respuesta inválida.",
            http_status=response.status_code,
        ) from error

    if not response.ok:

        error_data = (
            response_data.get("error") or {}
            if isinstance(response_data, dict)
            else {}
        )

        if not isinstance(error_data, dict):
            error_data = {}

        raise MetaEmbeddedSignupError(
            "Meta rechazó el código de autorización.",
            http_status=response.status_code,
            meta_error_code=error_data.get("code"),
            meta_error_subcode=error_data.get("error_subcode"),
        )

    access_token = (
        response_data.get("access_token")
        if isinstance(response_data, dict)
        else None
    )

    if not access_token:
        raise MetaEmbeddedSignupError(
            "Meta no devolvió un access token."
        )

    logger.info(
        "[EMBEDDED SIGNUP] AUTHORIZATION EXCHANGE COMPLETED"
    )

    return access_token


def request_authorized_meta_resource(
    access_token,
    resource_path,
    fields=None,
    params=None,
    method="GET",
):
    """
        DOCSTRING: Request Authorized Meta Resource

        Description:
        - Execute an authenticated Meta Graph API request.

        Notes:
        - Authorization uses the Bearer header.
        - Credentials and raw responses are never logged.
        - Supports GET and POST requests.
    """

    request_params = dict(params or {})

    if fields:
        request_params["fields"] = fields

    try:
        response = requests.request(
            method=method,
            url=get_embedded_signup_graph_url(
                resource_path
            ),
            headers={
                "Authorization": f"Bearer {access_token}",
            },
            params=request_params,
            timeout=getattr(
                settings,
                "DIALOQO_META_REQUEST_TIMEOUT",
                30,
            ),
        )

    except requests.RequestException as error:
        raise MetaEmbeddedSignupError(
            "No fue posible consultar Meta."
        ) from error

    try:
        response_data = (
            response.json()
            if response.content
            else {}
        )

    except ValueError as error:
        raise MetaEmbeddedSignupError(
            "Meta devolvió JSON inválido.",
            http_status=response.status_code,
        ) from error

    if not response.ok:

        error_data = (
            response_data.get("error") or {}
            if isinstance(response_data, dict)
            else {}
        )

        if not isinstance(error_data, dict):
            error_data = {}

        raise MetaEmbeddedSignupError(
            "Meta rechazó la operación.",
            http_status=response.status_code,
            meta_error_code=error_data.get("code"),
            meta_error_subcode=error_data.get("error_subcode"),
        )

    if not isinstance(response_data, dict):
        raise MetaEmbeddedSignupError(
            "Meta devolvió un formato inesperado."
        )

    return response_data


def validate_embedded_signup_token(
    access_token,
    meta_waba_id,
):
    """
        DOCSTRING: Validate Embedded Signup Token

        Description:
        - Validate the token through Meta debug_token.
        - Confirm application ownership.
        - Verify required WhatsApp permissions.
        - Validate expiration information.
        - Check granular WABA permissions when available.

        Notes:
        - Uses the Dialoqo App ID and App Secret.
        - No access tokens are logged.
        - Successful checks do not guarantee future token validity.
        - Granular permissions are evaluated when Meta returns them.
    """

    logger.info(
        "[EMBEDDED SIGNUP] TOKEN VALIDATION START | "
        "waba_id=%s",
        meta_waba_id,
    )

    app_access_token = (
        f"{get_meta_app_id()}|"
        f"{get_meta_app_secret()}"
    )

    response_data = request_authorized_meta_resource(
        access_token=app_access_token,
        resource_path="debug_token",
        params={
            "input_token": access_token,
        },
    )

    token_data = response_data.get("data") or {}

    if not isinstance(token_data, dict):
        raise ValidationError({
            "authorization":
                "Meta devolvió información de token inválida."
        })

    if not token_data.get("is_valid"):
        raise ValidationError({
            "authorization":
                "El token no es válido en Meta."
        })

    if str(token_data.get("app_id")) != str(
        get_meta_app_id()
    ):
        raise ValidationError({
            "authorization":
                "El token no pertenece a la aplicación Dialoqo."
        })

    granted_permissions = set(
        token_data.get("scopes") or []
    )

    missing_permissions = (
        REQUIRED_META_PERMISSIONS - granted_permissions
    )

    if missing_permissions:
        raise ValidationError({
            "authorization":
                "El token no tiene los permisos requeridos: "
                + ", ".join(sorted(missing_permissions))
        })

    granular_scopes = (
        token_data.get("granular_scopes") or []
    )

    for permission in REQUIRED_META_PERMISSIONS:

        matching_scopes = [
            item
            for item in granular_scopes
            if isinstance(item, dict)
            and item.get("scope") == permission
        ]

        for scope in matching_scopes:

            target_ids = scope.get("target_ids")

            if target_ids is None:
                continue

            authorized_ids = {
                str(value)
                for value in target_ids
            }

            if str(meta_waba_id) not in authorized_ids:
                raise ValidationError({
                    "authorization":
                        "El token no tiene autorización granular "
                        "para la WABA seleccionada."
                })

    expires_at = None

    expiration_timestamp = token_data.get(
        "expires_at"
    )

    if expiration_timestamp is not None:

        try:
            expiration_timestamp = int(
                expiration_timestamp
            )

        except (TypeError, ValueError) as error:
            raise ValidationError({
                "authorization":
                    "Meta devolvió una expiración inválida."
            }) from error

        if expiration_timestamp > 0:

            try:
                expires_at = datetime.fromtimestamp(
                    expiration_timestamp,
                    tz=datetime_timezone.utc,
                )

            except (OverflowError, OSError, ValueError) as error:
                raise ValidationError({
                    "authorization":
                        "Meta devolvió una expiración fuera de rango."
                }) from error

            if expires_at <= timezone.now():
                raise ValidationError({
                    "authorization":
                        "El token de Meta ha expirado."
                })

    logger.info(
        "[EMBEDDED SIGNUP] TOKEN VALIDATED | "
        "waba_id=%s | "
        "permissions_verified=True | "
        "expiration_known=%s",
        meta_waba_id,
        expires_at is not None,
    )

    return {
        "is_valid": True,
        "expires_at": expires_at,
        "permissions": sorted(granted_permissions),
    }


def validate_embedded_signup_waba(
    access_token,
    meta_waba_id,
):
    """
        DOCSTRING: Validate Embedded Signup WABA

        Description:
        - Confirm authorized access to the selected WABA.
        - Retrieve WABA identity and ownership information.
    """

    if not meta_waba_id:
        raise ValidationError({
            "waba_id": "Meta no proporcionó WABA ID."
        })

    meta_waba = request_authorized_meta_resource(
        access_token=access_token,
        resource_path=meta_waba_id,
        fields="id,name,owner_business_info",
    )

    if str(meta_waba.get("id")) != str(
        meta_waba_id
    ):
        raise ValidationError({
            "waba_id":
                "Meta devolvió una WABA diferente."
        })

    logger.info(
        "[EMBEDDED SIGNUP] WABA VALIDATED | "
        "waba_id=%s",
        meta_waba_id,
    )

    return meta_waba


def resolve_embedded_signup_business_id(meta_waba):
    """
        DOCSTRING: Resolve Embedded Signup Business ID

        Description:
        - Resolve the owning Meta Business Portfolio ID.

        Notes:
        - Ownership information must originate from Meta.
        - The frontend is not authoritative.
    """

    owner_business_info = (
        meta_waba.get("owner_business_info") or {}
    )

    business_id = (
        owner_business_info.get("id")
        if isinstance(owner_business_info, dict)
        else None
    )

    if not business_id:
        raise ValidationError({
            "business_id":
                "Meta no proporcionó información suficiente "
                "para verificar el Business Portfolio."
        })

    return str(business_id)


def get_embedded_signup_phone_numbers(
    access_token,
    meta_waba_id,
):
    """
        DOCSTRING: Get Embedded Signup Phone Numbers

        Description:
        - Discover all numbers belonging to the authorized WABA.
        - Follow Meta cursor pagination.

        Notes:
        - Numbers are discovered but not registered locally.
        - Phone numbers are never logged.
    """

    phone_numbers = []
    after = None
    visited_cursors = set()

    while True:

        params = {
            "limit": 100,
        }

        if after:
            params["after"] = after

        response_data = request_authorized_meta_resource(
            access_token=access_token,
            resource_path=(
                f"{meta_waba_id}/phone_numbers"
            ),
            fields=(
                "id,"
                "display_phone_number,"
                "verified_name,"
                "code_verification_status"
            ),
            params=params,
        )

        page_numbers = (
            response_data.get("data") or []
        )

        if not isinstance(page_numbers, list):
            raise MetaEmbeddedSignupError(
                "Meta devolvió una lista de números inválida."
            )

        phone_numbers.extend(page_numbers)

        paging = response_data.get("paging") or {}
        cursors = paging.get("cursors") or {}

        next_url = paging.get("next")
        next_cursor = cursors.get("after")

        if not next_url or not next_cursor:
            break

        if next_cursor in visited_cursors:
            raise MetaEmbeddedSignupError(
                "Meta devolvió una paginación repetida."
            )

        visited_cursors.add(next_cursor)
        after = next_cursor

    logger.info(
        "[EMBEDDED SIGNUP] PHONE DISCOVERY COMPLETED | "
        "waba_id=%s | count=%s",
        meta_waba_id,
        len(phone_numbers),
    )

    return phone_numbers


def validate_embedded_signup_phone_number(
    phone_numbers,
    meta_phone_number_id,
):
    """
        DOCSTRING: Validate Embedded Signup Phone Number

        Description:
        - Verify the selected Phone Number ID belongs to the WABA.
    """

    if not meta_phone_number_id:
        raise ValidationError({
            "phone_number_id":
                "Meta no proporcionó Phone Number ID."
        })

    for phone_data in phone_numbers:

        if str(phone_data.get("id")) == str(
            meta_phone_number_id
        ):
            return phone_data

    raise ValidationError({
        "phone_number_id":
            "El número no pertenece a la WABA autorizada."
    })


def validate_embedded_signup_operational_access(
    access_token,
    meta_waba_id,
):
    """
        DOCSTRING: Validate Embedded Signup Operational Access

        Description:
        - Confirm the authorized token can access the WABA.
    """

    meta_waba = request_authorized_meta_resource(
        access_token=access_token,
        resource_path=meta_waba_id,
        fields="id,name",
    )

    if str(meta_waba.get("id")) != str(
        meta_waba_id
    ):
        raise ValidationError({
            "waba_id":
                "No se confirmó acceso operativo a la WABA."
        })

    return True


def subscribe_embedded_signup_waba(
    access_token,
    meta_waba_id,
):
    """
        DOCSTRING: Subscribe Embedded Signup WABA

        Description:
        - Subscribe Dialoqo to the authorized WABA.
        - Verify the subscription through Meta.

        Notes:
        - This operation modifies external Meta configuration.
        - Django database rollback cannot reverse it.
    """

    logger.info(
        "[EMBEDDED SIGNUP] WEBHOOK SUBSCRIPTION START | "
        "waba_id=%s",
        meta_waba_id,
    )

    request_authorized_meta_resource(
        access_token=access_token,
        resource_path=(
            f"{meta_waba_id}/subscribed_apps"
        ),
        method="POST",
    )

    response_data = request_authorized_meta_resource(
        access_token=access_token,
        resource_path=(
            f"{meta_waba_id}/subscribed_apps"
        ),
    )

    subscribed_apps = (
        response_data.get("data") or []
    )

    if not payload_contains_meta_id(
        subscribed_apps,
        get_meta_app_id(),
    ):
        raise ValidationError({
            "webhook":
                "Meta no confirmó la suscripción de Dialoqo."
        })

    logger.info(
        "[EMBEDDED SIGNUP] WEBHOOK SUBSCRIPTION CONFIRMED | "
        "waba_id=%s",
        meta_waba_id,
    )

    return subscribed_apps
