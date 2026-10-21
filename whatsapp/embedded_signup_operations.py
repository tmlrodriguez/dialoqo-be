import logging

from datetime import datetime, timezone as datetime_timezone

import requests

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from auditing.operations import schedule_audit_event
from auditing.registry import (
    AUDIT_ACTION_REGISTRY,
    AUDIT_CATEGORY_REGISTRY,
)

from .credentials import (
    get_meta_app_id,
    get_meta_credentials,
)

from .credential_storage import get_credential_storage

from .meta_lifecycle import payload_contains_meta_id

from .models import WhatsAppBusinessAccount

from .number_registration_operations import (
    register_discovered_whatsapp_numbers,
)


logger = logging.getLogger(__name__)


REQUIRED_META_PERMISSIONS = {
    "whatsapp_business_management",
    "whatsapp_business_messaging",
}


# Define your Embedded Signup operations here.


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

    credentials = get_meta_credentials()

    try:
        response = requests.get(
            get_embedded_signup_graph_url(
                "oauth/access_token"
            ),
            params={
                "client_id": get_meta_app_id(),
                "client_secret": credentials["app_secret"],
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

    credentials = get_meta_credentials()

    app_access_token = (
        f"{get_meta_app_id()}|"
        f"{credentials['app_secret']}"
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


def register_embedded_signup_waba(
    company,
    actor,
    meta_waba,
    meta_business_id,
):
    """
        DOCSTRING: Register Embedded Signup WABA

        Description:
        - Create or reuse a WABA belonging to the selected company.
        - Preserve tenant ownership.

        Notes:
        - WABAs cannot be reassigned between companies.
        - Existing inactive WABAs are rejected.
    """

    meta_waba_id = str(
        meta_waba["id"]
    )

    with transaction.atomic():

        existing_account = (
            WhatsAppBusinessAccount.objects
            .select_for_update()
            .filter(
                meta_waba_id=meta_waba_id
            )
            .first()
        )

        if existing_account:

            if existing_account.company_id != company.id:
                raise ValidationError({
                    "waba_id":
                        "La WABA pertenece a otra empresa."
                })

            if not existing_account.is_active:
                raise ValidationError({
                    "waba_id":
                        "La WABA está desactivada."
                })

            if str(
                existing_account.meta_business_id
            ) != str(meta_business_id):
                raise ValidationError({
                    "business_id":
                        "El Business Portfolio ID no coincide."
                })

            return existing_account, False

        account = WhatsAppBusinessAccount.objects.create(
            company=company,
            meta_waba_id=meta_waba_id,
            meta_business_id=str(meta_business_id),
            display_name=(
                                meta_waba.get("name")
                or f"WhatsApp Business {meta_waba_id}"
            )[:150],
            is_connected=False,
            is_webhook_configured=False,
            created_by=actor,
            updated_by=actor,
        )

        schedule_audit_event(
            category=AUDIT_CATEGORY_REGISTRY.WHATSAPP,
            action=AUDIT_ACTION_REGISTRY.CREATE,
            description=(
                "Cuenta WABA registrada mediante Embedded Signup."
            ),
            actor=actor,
            company=company,
            target=account,
            metadata={
                "whatsapp_business_account_id": account.id,
                "meta_waba_id": meta_waba_id,
                "source": "EMBEDDED_SIGNUP",
            },
        )

    return account, True


def store_embedded_signup_credential(
    account,
    access_token,
    expires_at=None,
):
    """
        DOCSTRING: Store Embedded Signup Credential

        Description:
        - Encrypt and persist an authorized Meta access token.
        - Activate the credential after successful validation.
        - Preserve the expiration date returned by Meta.

        Notes:
        - The caller must validate the token before invoking
          this operation.
        - Plaintext tokens are never persisted.
        - Newly stored credentials initially remain inactive.
        - Credential activation occurs within the database transaction.
        - Access tokens are never logged or included in audit events.
    """

    logger.info(
        "[EMBEDDED SIGNUP] CREDENTIAL STORAGE START | "
        "account_id=%s",
        account.id,
    )

    storage = get_credential_storage()

    credential = storage.store(
        whatsapp_business_account=account,
        access_token=access_token,
    )

    credential.is_active = True
    credential.is_valid = True
    credential.last_validated_at = timezone.now()
    credential.expires_at = expires_at

    credential.save(
        update_fields=[
            "is_active",
            "is_valid",
            "last_validated_at",
            "expires_at",
            "updated_at",
        ]
    )

    logger.info(
        "[EMBEDDED SIGNUP] CREDENTIAL STORED | "
        "account_id=%s | credential_id=%s | "
        "active=True | expiration_known=%s",
        account.id,
        credential.id,
        expires_at is not None,
    )

    return credential


def complete_whatsapp_embedded_signup(
    company,
    actor,
    authorization_code,
    meta_waba_id,
    meta_phone_number_id,
    meta_business_id=None,
):
    """
        DOCSTRING: Complete WhatsApp Embedded Signup

        Description:
        - Exchange the Meta authorization code.
        - Validate the token through Meta debug_token.
        - Verify application identity and required permissions.
        - Validate token expiration and WABA access.
        - Resolve Business Portfolio ownership.
        - Discover and validate WhatsApp phone numbers.
        - Subscribe Dialoqo to WABA webhook events.
        - Register or reuse the WABA.
        - Encrypt and persist the operational credential.
        - Register the selected WhatsApp number under Principal.
        - Synchronize connection state.
        - Record centralized audit events.

        Notes:
        - Company authorization must be enforced by the API view.
        - Authorization codes and access tokens are never logged.
        - The frontend Business Portfolio ID is not authoritative.
        - Only the selected number is registered automatically.
        - Existing number assignments and branches are preserved.
        - Monitoring is not activated automatically.
        - Coexistence requires subsequent messaging tests.
        - External Meta operations cannot be rolled back by Django.
    """

    if not company or not company.is_active:
        raise ValidationError({
            "company": "La empresa no está disponible."
        })

    if not actor or not actor.is_active:
        raise ValidationError({
            "actor": "El usuario administrador no está activo."
        })

    logger.info(
        "[EMBEDDED SIGNUP] ONBOARDING START | "
        "company_id=%s | actor_id=%s | "
        "waba_id=%s | phone_number_id=%s",
        company.id,
        actor.id,
        meta_waba_id,
        meta_phone_number_id,
    )

    try:

        # ---------------------------------------------------------
        # STEP 1: Exchange authorization code
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP] STEP 1 | "
            "Exchanging authorization code"
        )

        access_token = exchange_embedded_signup_authorization_code(
            authorization_code
        )

        # ---------------------------------------------------------
        # STEP 2: Validate token permissions and expiration
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP] STEP 2 | "
            "Validating Meta authorization"
        )

        token_validation = validate_embedded_signup_token(
            access_token=access_token,
            meta_waba_id=meta_waba_id,
        )

        token_expires_at = token_validation.get(
            "expires_at"
        )

        logger.info(
            "[EMBEDDED SIGNUP] TOKEN AUTHORIZATION VERIFIED | "
            "waba_id=%s | expiration_known=%s",
            meta_waba_id,
            token_expires_at is not None,
        )

        # ---------------------------------------------------------
        # STEP 3: Validate authorized WABA
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP] STEP 3 | "
            "Validating authorized WABA"
        )

        meta_waba = validate_embedded_signup_waba(
            access_token=access_token,
            meta_waba_id=meta_waba_id,
        )

        # ---------------------------------------------------------
        # STEP 4: Resolve Business Portfolio ownership
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP] STEP 4 | "
            "Resolving Business Portfolio ownership"
        )

        resolved_business_id = resolve_embedded_signup_business_id(
            meta_waba=meta_waba
        )

        if (
            meta_business_id
            and str(meta_business_id) != str(resolved_business_id)
        ):
            raise ValidationError({
                "business_id":
                    "El Business Portfolio ID proporcionado "
                    "no coincide con el propietario verificado en Meta."
            })

        logger.info(
            "[EMBEDDED SIGNUP] BUSINESS OWNERSHIP VERIFIED | "
            "waba_id=%s | business_id=%s",
            meta_waba_id,
            resolved_business_id,
        )

        # ---------------------------------------------------------
        # STEP 5: Discover authorized phone numbers
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP] STEP 5 | "
            "Discovering authorized phone numbers"
        )

        phone_numbers = get_embedded_signup_phone_numbers(
            access_token=access_token,
            meta_waba_id=meta_waba_id,
        )

        # ---------------------------------------------------------
        # STEP 6: Validate selected Phone Number ID
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP] STEP 6 | "
            "Validating selected Phone Number ID"
        )

        selected_phone = validate_embedded_signup_phone_number(
            phone_numbers=phone_numbers,
            meta_phone_number_id=meta_phone_number_id,
        )

        # ---------------------------------------------------------
        # STEP 7: Validate operational WABA access
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP] STEP 7 | "
            "Validating operational WABA access"
        )

        validate_embedded_signup_operational_access(
            access_token=access_token,
            meta_waba_id=meta_waba_id,
        )

        # ---------------------------------------------------------
        # STEP 8: Subscribe Dialoqo to Meta webhooks
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP] STEP 8 | "
            "Subscribing application to WABA"
        )

        subscribe_embedded_signup_waba(
            access_token=access_token,
            meta_waba_id=meta_waba_id,
        )

        # ---------------------------------------------------------
        # STEP 9: Register WABA, credential, and selected number
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP] STEP 9 | "
            "Registering WABA, credential, and selected number"
        )

        now = timezone.now()

        with transaction.atomic():

            account, created = register_embedded_signup_waba(
                company=company,
                actor=actor,
                meta_waba=meta_waba,
                meta_business_id=resolved_business_id,
            )

            credential = store_embedded_signup_credential(
                account=account,
                access_token=access_token,
                expires_at=token_expires_at,
            )

            # -----------------------------------------------------
            # STEP 10: Synchronize WABA connection state
            # -----------------------------------------------------

            locked_account = (
                WhatsAppBusinessAccount.objects
                .select_for_update()
                .select_related("company")
                .get(id=account.id)
            )

            locked_account.is_connected = True
            locked_account.is_webhook_configured = True

            locked_account.connected_at = (
                locked_account.connected_at or now
            )

            locked_account.disconnected_at = None
            locked_account.updated_by = actor

            locked_account.save(
                update_fields=[
                    "is_connected",
                    "is_webhook_configured",
                    "connected_at",
                    "disconnected_at",
                    "updated_by",
                    "updated_at",
                ]
            )

            # -----------------------------------------------------
            # STEP 11: Register selected WhatsApp number
            # -----------------------------------------------------

            logger.info(
                "[EMBEDDED SIGNUP] STEP 11 | "
                "Registering selected WhatsApp number"
            )

            registration_result = register_discovered_whatsapp_numbers(
                company=company,
                actor=actor,
                whatsapp_business_account=locked_account,
                phone_numbers=[selected_phone],
            )

            logger.info(
                "[EMBEDDED SIGNUP] NUMBER REGISTRATION COMPLETED | "
                "account_id=%s | created=%s | reused=%s",
                locked_account.id,
                registration_result["created_count"],
                registration_result["reused_count"],
            )

            # -----------------------------------------------------
            # STEP 12: Record centralized audit event
            # -----------------------------------------------------

            schedule_audit_event(
                category=AUDIT_CATEGORY_REGISTRY.META,
                action=AUDIT_ACTION_REGISTRY.CONNECT,
                description=(
                    "Cuenta de WhatsApp Business conectada "
                    "mediante Meta Embedded Signup."
                ),
                actor=actor,
                company=company,
                target=locked_account,
                metadata={
                    "source": "EMBEDDED_SIGNUP",
                    "whatsapp_business_account_id":
                        locked_account.id,
                    "meta_waba_id":
                        locked_account.meta_waba_id,
                    "meta_business_id":
                        locked_account.meta_business_id,
                    "meta_phone_number_id":
                        str(meta_phone_number_id),
                    "credential_id":
                        credential.id,
                    "is_connected":
                        locked_account.is_connected,
                    "is_webhook_configured":
                        locked_account.is_webhook_configured,
                    "created":
                        created,
                    "discovered_phone_numbers":
                        len(phone_numbers),
                    "registered_numbers":
                        len(registration_result["numbers"]),
                    "created_numbers":
                        registration_result["created_count"],
                    "reused_numbers":
                        registration_result["reused_count"],
                    "token_expiration_known":
                        token_expires_at is not None,
                },
            )

        # ---------------------------------------------------------
        # STEP 13: Prepare discovered phone numbers
        # ---------------------------------------------------------

        discovered_numbers = []

        for phone_data in phone_numbers:

            discovered_numbers.append({
                "meta_phone_number_id": str(
                    phone_data.get("id") or ""
                ),
                "display_phone_number": (
                    phone_data.get("display_phone_number") or ""
                ),
                "verified_name": (
                    phone_data.get("verified_name") or ""
                ),
                "code_verification_status": (
                    phone_data.get("code_verification_status") or ""
                ),
                "is_selected": (
                    str(phone_data.get("id"))
                    ==
                    str(meta_phone_number_id)
                ),
            })

        # ---------------------------------------------------------
        # STEP 14: Prepare registered phone numbers
        # ---------------------------------------------------------

        registered_numbers = []

        for item in registration_result["numbers"]:

            number = item["whatsapp_number"]

            registered_numbers.append({
                "id": number.id,
                "meta_phone_number_id":
                    number.meta_phone_number_id,
                "branch_id": number.branch_id,
                "created": item["created"],
            })

        # ---------------------------------------------------------
        # STEP 15: Complete onboarding
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP] ONBOARDING COMPLETED | "
            "company_id=%s | "
            "account_id=%s | "
            "waba_id=%s | "
            "phone_number_id=%s | "
            "created=%s | "
            "connected=%s | "
            "webhook=%s | "
            "registered_numbers=%s",
            company.id,
            locked_account.id,
            locked_account.meta_waba_id,
            meta_phone_number_id,
            created,
            locked_account.is_connected,
            locked_account.is_webhook_configured,
            len(registered_numbers),
        )

        return {
            "whatsapp_business_account": locked_account,
            "created": created,
            "meta_waba": meta_waba,
            "meta_business_id": str(resolved_business_id),
            "selected_phone": {
                "meta_phone_number_id": str(
                    selected_phone.get("id")
                ),
                "display_phone_number": (
                    selected_phone.get("display_phone_number") or ""
                ),
                "verified_name": (
                    selected_phone.get("verified_name") or ""
                ),
                "code_verification_status": (
                    selected_phone.get("code_verification_status") or ""
                ),
            },
            "phone_numbers": discovered_numbers,
            "registered_numbers": registered_numbers,
            "created_numbers": registration_result["created_count"],
            "reused_numbers": registration_result["reused_count"],
            "is_connected": locked_account.is_connected,
            "is_webhook_configured":
                locked_account.is_webhook_configured,
            "credential_validated": True,
            "credential_expires_at": (
                token_expires_at.isoformat()
                if token_expires_at
                else None
            ),
        }

    except (
        MetaEmbeddedSignupError,
        ValidationError,
    ) as error:

        diagnostic_data = {
            "company_id": company.id,
            "actor_id": actor.id,
            "waba_id": meta_waba_id,
            "phone_number_id": meta_phone_number_id,
            "error_type": type(error).__name__,
        }

        if isinstance(error, MetaEmbeddedSignupError):
            diagnostic_data.update({
                "http_status": error.http_status,
                "meta_error_code": error.meta_error_code,
                "meta_error_subcode": error.meta_error_subcode,
            })

        logger.error(
            "[EMBEDDED SIGNUP] ONBOARDING FAILED | %s",
            diagnostic_data,
        )

        raise