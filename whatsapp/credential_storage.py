from abc import ABC, abstractmethod

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from .credential_encryption import (
    CredentialEncryptionError,
    decrypt_credential,
    encrypt_credential,
)


# Define your credential storage operations here.


class CredentialStorageError(Exception):
    """
        DOCSTRING: Credential Storage Error

        Description:
        - Represent a controlled failure while storing,
          retrieving, or revoking sensitive Meta credentials.

        Notes:
        - Credential values are never included in exceptions.
        - Sensitive information must never be logged.
        - Invalid credentials must never be returned.
    """

    pass


class BaseCredentialStorage(ABC):
    """
        DOCSTRING: Base Credential Storage

        Description:
        - Define the storage interface used by Dialoqo
          for Meta operational credentials.

        Notes:
        - Storage implementations must enforce WABA isolation.
        - Credentials must never be exposed through normal APIs.
        - Production implementations may use a managed
          secrets-management service.
    """

    @abstractmethod
    def store(self, whatsapp_business_account, access_token):
        """
        Store an operational access token for a WABA.
        """

    @abstractmethod
    def retrieve(self, whatsapp_business_account):
        """
        Retrieve a validated operational token for a WABA.
        """

    @abstractmethod
    def delete(self, whatsapp_business_account):
        """
        Revoke the locally stored credential for a WABA.
        """


class DatabaseCredentialStorage(BaseCredentialStorage):
    """
        DOCSTRING: Database Credential Storage

        Description:
        - Store encrypted Meta credentials in PostgreSQL.
        - Retrieve only active and validated credentials.
        - Support secure credential replacement and revocation.

        Notes:
        - Intended for the current development environment.
        - Encryption uses the configured Fernet key.
        - Plaintext tokens are never persisted.
        - Each credential belongs to one WABA.
        - Newly stored credentials require explicit validation.
        - Credential replacement invalidates previous validation.
        - Database transactions protect local state consistency.
    """

    def _validate_account(self, whatsapp_business_account):
        """
            DOCSTRING: Validate Account

            Description:
            - Validate that a persisted WABA was provided.

            Notes:
            - The caller remains responsible for company-level
              authorization and tenant isolation.
        """

        account_id = getattr(
            whatsapp_business_account,
            "pk",
            None,
        )

        if not account_id:
            raise CredentialStorageError(
                "La cuenta de WhatsApp Business no es válida."
            )

        return account_id

    @transaction.atomic
    def store(self, whatsapp_business_account, access_token):
        """
            DOCSTRING: Store Credential

            Description:
            - Encrypt and persist an operational Meta access token.
            - Create or replace the credential associated with a WABA.
            - Require explicit validation before operational use.

            Notes:
            - The owning WABA must already exist.
            - Plaintext tokens are never persisted.
            - New credentials start inactive and invalid.
            - Replacing a token resets previous validation metadata.
            - The operation does not verify permissions against Meta.
        """

        account_id = self._validate_account(
            whatsapp_business_account
        )

        if (
            not isinstance(access_token, str)
            or not access_token.strip()
        ):
            raise CredentialStorageError(
                "La credencial proporcionada no es válida."
            )

        from .models import MetaCredential

        try:
            encrypted_token = encrypt_credential(
                access_token
            )

        except (
            CredentialEncryptionError,
            ImproperlyConfigured,
        ) as error:
            raise CredentialStorageError(
                "No fue posible cifrar la credencial."
            ) from error

        credential, created = (
            MetaCredential.objects.update_or_create(
                whatsapp_business_account_id=account_id,
                defaults={
                    "encrypted_access_token": encrypted_token,
                    "is_active": False,
                    "is_valid": False,
                    "last_validated_at": None,
                    "expires_at": None,
                },
            )
        )

        return credential

    def retrieve(self, whatsapp_business_account):
        """
            DOCSTRING: Retrieve Credential

            Description:
            - Retrieve and decrypt the operational credential
              associated with the supplied WABA.

            Notes:
            - Inactive credentials cannot be retrieved.
            - Invalid credentials cannot be retrieved.
            - Expired credentials cannot be retrieved.
            - Missing credentials produce a controlled error.
            - Decryption failures never expose sensitive values.
        """

        account_id = self._validate_account(
            whatsapp_business_account
        )

        from .models import MetaCredential

        credential = MetaCredential.objects.filter(
            whatsapp_business_account_id=account_id,
        ).first()

        if credential is None:
            raise CredentialStorageError(
                "No existe una credencial operativa para esta WABA."
            )

        if not credential.is_active:
            raise CredentialStorageError(
                "La credencial operativa se encuentra desactivada."
            )

        if not credential.is_valid:
            raise CredentialStorageError(
                "La credencial operativa no se encuentra validada."
            )

        if (
            credential.expires_at is not None
            and credential.expires_at <= timezone.now()
        ):
            raise CredentialStorageError(
                "La credencial operativa ha expirado."
            )

        if not credential.encrypted_access_token:
            raise CredentialStorageError(
                "La credencial operativa no está disponible."
            )

        try:
            return decrypt_credential(
                credential.encrypted_access_token
            )

        except (
            CredentialEncryptionError,
            ImproperlyConfigured,
        ) as error:
            raise CredentialStorageError(
                "No fue posible recuperar la credencial operativa."
            ) from error

    @transaction.atomic
    def delete(self, whatsapp_business_account):
        """
            DOCSTRING: Delete Credential

            Description:
            - Revoke local access to the credential associated
              with a WhatsApp Business Account.
            - Clear the encrypted token.
            - Preserve the credential record for traceability.

            Notes:
            - This operation does not revoke the token at Meta.
            - Historical credential metadata remains available.
            - Revoked credentials cannot be retrieved.
        """

        account_id = self._validate_account(
            whatsapp_business_account
        )

        from .models import MetaCredential

        credential = (
            MetaCredential.objects
            .select_for_update()
            .filter(
                whatsapp_business_account_id=account_id,
            )
            .first()
        )

        if credential is None:
            return False

        credential.encrypted_access_token = ""
        credential.is_active = False
        credential.is_valid = False

        credential.save(
            update_fields=[
                "encrypted_access_token",
                "is_active",
                "is_valid",
                "updated_at",
            ]
        )

        return True


def get_credential_storage():
    """
        DOCSTRING: Get Credential Storage

        Description:
        - Resolve the configured Meta credential storage backend.

        Notes:
        - Database storage is used during development.
        - Unsupported storage backends are rejected explicitly.
        - Production implementations can be introduced later.
        - Business operations remain independent of storage type.
    """

    backend = getattr(
        settings,
        "DIALOQO_CREDENTIAL_STORAGE_BACKEND",
        "database",
    )

    if backend == "database":
        return DatabaseCredentialStorage()

    raise ImproperlyConfigured(
        "El backend de almacenamiento de credenciales "
        "configurado no está disponible."
    )