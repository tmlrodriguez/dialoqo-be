import os

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone


# Define your Meta credential helpers here.


def _get_required_meta_setting(name):
    """
        DOCSTRING: Get Required Meta Setting

        Description:
        - Resolve a required global Meta configuration value.

        Notes:
        - Sensitive credentials remain on the backend.
        - Missing configuration raises ImproperlyConfigured.
    """

    value = os.environ.get(name)

    if not value:
        raise ImproperlyConfigured(
            f"Configuración de Meta rechazada: "
            f"{name} no se encuentra configurado."
        )

    return value


def get_meta_app_id():
    """
        DOCSTRING: Get Meta App ID

        Description:
        - Return the global Meta App ID used by Dialoqo.
    """

    return _get_required_meta_setting(
        "DIALOQO_META_APP_ID"
    )


def get_meta_credentials():
    """
        DOCSTRING: Get Meta Credentials

        Description:
        - Resolve the global Meta application credentials.

        Notes:
        - Dialoqo uses one Meta application.
        - The App Secret and Verify Token are global.
        - The existing System User token is preserved.
        - Customer credentials are never exposed to React.
    """

    return {
        "access_token": _get_required_meta_setting(
            "DIALOQO_META_ACCESS_TOKEN"
        ),
        "app_secret": _get_required_meta_setting(
            "DIALOQO_META_APP_SECRET"
        ),
        "verify_token": _get_required_meta_setting(
            "DIALOQO_META_VERIFY_TOKEN"
        ),
    }


def get_global_meta_access_token():
    """
        DOCSTRING: Get Global Meta Access Token

        Description:
        - Resolve the existing Telemática System User token.

        Notes:
        - The token is configured globally.
        - This function does not establish WABA authorization.
    """

    return _get_required_meta_setting(
        "DIALOQO_META_ACCESS_TOKEN"
    )


def get_legacy_meta_waba_ids():
    """
        DOCSTRING: Get Legacy Meta WABA IDs

        Description:
        - Resolve the explicit allowlist of existing WABAs
          authorized to use the global System User token.

        Notes:
        - WABA identifiers are configured on the backend.
        - Newly onboarded WABAs are not automatically included.
        - An empty allowlist disables WABA-specific fallback.
    """

    configured_ids = os.getenv(
        "DIALOQO_META_LEGACY_WABA_IDS",
        "",
    )

    return {
        value.strip()
        for value in configured_ids.split(",")
        if value.strip()
    }


def get_meta_access_token(whatsapp_business_account=None):
    """
        DOCSTRING: Get Meta Access Token

        Description:
        - Resolve the operational Meta access token.
        - Prefer the encrypted credential associated with a WABA.
        - Preserve explicitly authorized legacy integrations.
        - Reject missing, inactive, invalid, or expired
          customer credentials.

        Notes:
        - Credentials are resolved exclusively on the backend.
        - Access tokens are never logged.
        - A revoked credential never falls back to the global token.
        - Unknown WABAs cannot use the global token.
        - No WABA context preserves platform-level compatibility.
        - Local validity flags do not replace Meta authorization checks.
    """

    if whatsapp_business_account is None:
        return get_global_meta_access_token()

    from .models import MetaCredential

    from .credential_storage import (
        CredentialStorageError,
        get_credential_storage,
    )

    waba_id = getattr(
        whatsapp_business_account,
        "meta_waba_id",
        None,
    )

    account_id = getattr(
        whatsapp_business_account,
        "pk",
        None,
    )

    if not account_id or not waba_id:
        raise ImproperlyConfigured(
            "No fue posible identificar la cuenta WABA."
        )

    credential = MetaCredential.objects.filter(
        whatsapp_business_account_id=account_id,
    ).first()

    if credential is not None:

        if not credential.is_active:
            raise ImproperlyConfigured(
                "La credencial operativa de esta WABA "
                "se encuentra desactivada."
            )

        if not credential.is_valid:
            raise ImproperlyConfigured(
                "La credencial operativa de esta WABA "
                "no se encuentra validada."
            )

        if (
            credential.expires_at is not None
            and credential.expires_at <= timezone.now()
        ):
            raise ImproperlyConfigured(
                "La credencial operativa de esta WABA "
                "ha expirado."
            )

        if not credential.encrypted_access_token:
            raise ImproperlyConfigured(
                "La credencial operativa de esta WABA "
                "no se encuentra disponible."
            )

        try:
            return get_credential_storage().retrieve(
                whatsapp_business_account
            )

        except CredentialStorageError as error:
            raise ImproperlyConfigured(
                "No fue posible recuperar la credencial "
                "operativa de esta WABA."
            ) from error

    if str(waba_id) in get_legacy_meta_waba_ids():
        return get_global_meta_access_token()

    raise ImproperlyConfigured(
        "Esta WABA no tiene una credencial operativa "
        "configurada. Complete la autorización mediante Meta."
    )