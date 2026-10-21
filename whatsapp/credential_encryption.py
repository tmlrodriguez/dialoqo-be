from cryptography.fernet import Fernet, InvalidToken

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


# Define your credential encryption helpers here.


class CredentialEncryptionError(Exception):
    """
        DOCSTRING: Credential Encryption Error

        Description:
        - Represent a controlled credential encryption
          or decryption failure.

        Notes:
        - Sensitive credential values are never included
          in exception messages.
    """

    pass


def get_credential_cipher():
    """
        DOCSTRING: Get Credential Cipher

        Description:
        - Initialize the authenticated encryption mechanism
          used for sensitive Meta credentials.

        Notes:
        - Encryption keys are resolved from Django settings.
        - Encryption keys are never stored in PostgreSQL.
        - Missing or invalid keys prevent credential operations.
    """

    encryption_key = getattr(
        settings,
        "DIALOQO_CREDENTIAL_ENCRYPTION_KEY",
        None,
    )

    if not encryption_key:
        raise ImproperlyConfigured(
            "La clave de cifrado de credenciales "
            "de Dialoqo no está configurada."
        )

    try:
        return Fernet(
            encryption_key.encode("utf-8")
            if isinstance(encryption_key, str)
            else encryption_key
        )

    except (ValueError, TypeError) as error:
        raise ImproperlyConfigured(
            "La clave de cifrado de credenciales "
            "de Dialoqo no es válida."
        ) from error


def encrypt_credential(value):
    """
        DOCSTRING: Encrypt Credential

        Description:
        - Encrypt a sensitive credential before persistence.

        Notes:
        - Empty credentials are rejected.
        - Plaintext credentials must never be logged.
        - The encrypted representation may be stored
          in PostgreSQL.
    """

    if not isinstance(value, str) or not value:
        raise CredentialEncryptionError(
            "No es posible cifrar una credencial vacía."
        )

    cipher = get_credential_cipher()

    try:
        encrypted_value = cipher.encrypt(
            value.encode("utf-8")
        )

        return encrypted_value.decode("utf-8")

    except Exception as error:
        raise CredentialEncryptionError(
            "No fue posible cifrar la credencial."
        ) from error


def decrypt_credential(encrypted_value):
    """
        DOCSTRING: Decrypt Credential

        Description:
        - Decrypt a previously encrypted credential.

        Notes:
        - Decryption occurs exclusively on the backend.
        - Decrypted credentials must never be returned
          through normal API responses.
        - Invalid or corrupted ciphertext is rejected.
    """

    if (
        not isinstance(encrypted_value, str)
        or not encrypted_value
    ):
        raise CredentialEncryptionError(
            "La credencial cifrada no es válida."
        )

    cipher = get_credential_cipher()

    try:
        decrypted_value = cipher.decrypt(
            encrypted_value.encode("utf-8")
        )

        return decrypted_value.decode("utf-8")

    except (InvalidToken, ValueError, TypeError) as error:
        raise CredentialEncryptionError(
            "No fue posible descifrar la credencial."
        ) from error