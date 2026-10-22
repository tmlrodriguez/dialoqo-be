"""Persistence of validated WABA operational credentials."""

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.utils import timezone

from whatsapp.meta.auth.credentials import get_meta_access_token
from whatsapp.meta.auth.encryption import get_credential_cipher
from whatsapp.meta.auth.storage import CredentialStorageError, get_credential_storage
from whatsapp.meta.lifecycle import MetaLifecycleClient
from whatsapp.models import MetaCredential, WhatsAppBusinessAccount


def prepare_waba_connection_client(account):
    """
        DOCSTRING: Prepare WABA Connection Client

        Description:
        - Resolve a client for administrative connection or number validation.
        - Allow a global token to validate a manually registered WABA that
          has no stored credential.

        Notes:
        - The returned flag identifies a credential that still needs storage.
        - The caller must verify WABA access before persisting that token.
        - Existing inactive, invalid, or expired credentials never fall back.
        - Messaging and read operations retain strict WABA resolution.
    """

    needs_credential = not MetaCredential.objects.filter(whatsapp_business_account=account).exists()

    if needs_credential:
        get_credential_cipher()

    client = MetaLifecycleClient(whatsapp_business_account=None if needs_credential else account)
    return client, needs_credential


@transaction.atomic
def store_validated_meta_credential(account, access_token, expires_at=None, replace_existing=True):
    """
        DOCSTRING: Store Validated Meta Credential

        Description:
        - Encrypt and activate a validated operational token for one WABA.
        - Preserve the expiration returned by Meta when available.

        Notes:
        - The caller must verify token acceptance and access to this WABA.
        - The account lock serializes credential replacement with onboarding.
        - Disabling replacement preserves concurrently created credentials
          and enforces their current validity instead of reactivating them.
        - Storage and activation commit together.
        - Plaintext tokens are never persisted or included in errors.
    """

    locked_account = WhatsAppBusinessAccount.objects.select_for_update().get(pk=account.pk)

    if (
        locked_account.meta_waba_id != account.meta_waba_id
        or locked_account.company_id != account.company_id
    ):
        raise ValidationError({
            "whatsapp_business_account":
                "La WABA cambió durante la validación. Vuelva a validar la cuenta."
        })

    if not replace_existing:
        credential = MetaCredential.objects.filter(whatsapp_business_account=locked_account).first()
        if credential is not None:
            get_meta_access_token(whatsapp_business_account=locked_account)
            return credential

    try:
        credential = get_credential_storage().store(
            whatsapp_business_account=locked_account,
            access_token=access_token,
        )
    except CredentialStorageError as error:
        raise ImproperlyConfigured(
            "No fue posible guardar la credencial operativa de esta WABA."
        ) from error

    credential.is_active = True
    credential.is_valid = True
    credential.last_validated_at = timezone.now()
    credential.expires_at = expires_at
    credential.save(update_fields=[
        "is_active",
        "is_valid",
        "last_validated_at",
        "expires_at",
        "updated_at",
    ])

    return credential
