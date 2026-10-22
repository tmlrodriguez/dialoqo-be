"""Media attachment persistence, validation, and private storage."""

import mimetypes
import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files import File
from django.db import transaction
from django.utils import timezone

from whatsapp.meta.auth.credentials import get_meta_access_token
from whatsapp.meta.media import (
    MetaMediaError,
    download_meta_media,
    get_meta_media_metadata,
)
from whatsapp.models import MediaAttachment
from whatsapp.operations.scheduling import schedule_media_attachment_storage
from whatsapp.registry import MEDIA_STORAGE_STATUS_REGISTRY
from whatsapp.utils.storage import private_whatsapp_media_storage


@transaction.atomic
def persist_media_attachment(message, media_payload):
    """
        DOCSTRING: Persist Media Attachment

        Description:
        - Persist normalized media metadata associated with a WhatsApp message.
        - Queue asynchronous retrieval and private storage of the media binary.

        Notes:
        - Binary media content is never downloaded inside webhook processing.
        - Duplicate webhook deliveries do not create duplicate attachment records.
        - Celery storage is scheduled only after successful database commit.
        - Already existing attachments may safely be rescheduled because media storage is idempotent.
    """

    if not media_payload:
        return None

    attachment, created = MediaAttachment.objects.get_or_create(
        message=message,
        meta_media_id=media_payload["meta_media_id"],
        defaults={
            "mime_type": media_payload["mime_type"],
            "original_filename": media_payload["original_filename"],
            "checksum": media_payload["checksum"],
        },
    )

    if created or not attachment.is_stored:
        schedule_media_attachment_storage(
            attachment=attachment,
        )

    return attachment


def build_storage_key(attachment, mime_type):
    """
        DOCSTRING: Build Storage Key

        Description:
        - Generate a unique private-storage key for a WhatsApp media attachment.

        Notes:
        - The key includes company and WhatsApp-number boundaries for operational organization.
        - User-provided filenames are never used directly as storage paths.
        - A generated UUID prevents storage collisions.
    """

    extension = ""

    if attachment.original_filename:
        candidate_extension = Path(attachment.original_filename).suffix.lower()

        if candidate_extension and len(candidate_extension) <= 10:
            extension = candidate_extension

    if not extension:
        extension = mimetypes.guess_extension(mime_type or "") or ""

    company_id = attachment.message.conversation.whatsapp_number.company_id
    number_id = attachment.message.conversation.whatsapp_number_id
    message_date = attachment.message.message_timestamp
    filename = f"{uuid.uuid4().hex}{extension}"

    return f"whatsapp/{company_id}/{number_id}/{message_date:%Y/%m}/{filename}"


def validate_media_metadata(attachment, metadata):
    """
        DOCSTRING: Validate Media Metadata

        Description:
        - Validate Meta media metadata against the Dialoqo attachment record and configured media limits.

        Notes:
        - The Meta media identifier must match the attachment identifier.
        - The reported file size must not exceed the configured maximum size.
        - MIME type is synchronized from Meta when available.
    """

    if str(metadata["id"]) != str(attachment.meta_media_id):
        raise MetaMediaError("Obtención de medio rechazada: el identificador del archivo recibido desde Meta no coincide.")

    file_size = metadata.get("file_size")

    if file_size is not None:
        try:
            file_size = int(file_size)
        except (TypeError, ValueError) as error:
            raise MetaMediaError("Obtención de medio rechazada: Meta devolvió un tamaño de archivo inválido.") from error

        if file_size > settings.DIALOQO_META_MEDIA_MAX_SIZE:
            raise MetaMediaError("Obtención de medio rechazada: el archivo excede el tamaño máximo permitido.")


def validate_downloaded_media(metadata, size, checksum):
    """
        DOCSTRING: Validate Downloaded Media

        Description:
        - Validate the downloaded media size and checksum against metadata reported by Meta.

        Notes:
        - Size validation is performed when Meta reports file_size.
        - SHA-256 validation is performed when Meta reports sha256.
        - Validation failures prevent the binary from being persisted.
    """

    reported_size = metadata.get("file_size")

    if reported_size is not None and int(reported_size) != size:
        raise MetaMediaError("Almacenamiento de medio rechazado: el tamaño descargado no coincide con el reportado por Meta.")

    reported_checksum = metadata.get("sha256") or ""

    if reported_checksum and reported_checksum.lower() != checksum.lower():
        raise MetaMediaError("Almacenamiento de medio rechazado: la verificación de integridad SHA-256 falló.")


@transaction.atomic
def store_media_attachment(attachment):
    """
        DOCSTRING: Store Media Attachment

        Description:
        - Retrieve a WhatsApp media binary from Meta and persist it in Dialoqo private storage.
        - Validate Meta metadata, file-size limits, and SHA-256 integrity before marking the attachment as stored.

        Notes:
        - Only the attachment row is locked to prevent concurrent duplicate downloads.
        - SELECT FOR UPDATE does not lock the joined message, conversation,
          number, or account rows.
        - Already stored media is returned without downloading again.
        - Failed retrieval attempts remain retryable.
        - Permanent Meta media URLs are never persisted.
    """

    locked_attachment = MediaAttachment.objects.select_for_update(of=("self",)).select_related("message__conversation__whatsapp_number__whatsapp_business_account").get(id=attachment.id)

    if locked_attachment.is_stored and locked_attachment.storage_key and private_whatsapp_media_storage.exists(locked_attachment.storage_key):
        return locked_attachment

    locked_attachment.retrieval_attempts += 1
    locked_attachment.storage_error = ""
    locked_attachment.storage_failed_at = None
    locked_attachment.save(update_fields=["retrieval_attempts", "storage_error", "storage_failed_at", "updated_at"])

    try:
        account = locked_attachment.message.conversation.whatsapp_number.whatsapp_business_account
        access_token = get_meta_access_token(whatsapp_business_account=account)
        metadata = get_meta_media_metadata(meta_media_id=locked_attachment.meta_media_id, access_token=access_token)
        validate_media_metadata(attachment=locked_attachment, metadata=metadata)

        temporary_file, size, checksum = download_meta_media(
            media_url=metadata["url"],
            access_token=access_token,
            maximum_size=settings.DIALOQO_META_MEDIA_MAX_SIZE,
        )

        try:
            validate_downloaded_media(metadata=metadata, size=size, checksum=checksum)

            mime_type = metadata.get("mime_type") or locked_attachment.mime_type or "application/octet-stream"
            storage_key = build_storage_key(attachment=locked_attachment, mime_type=mime_type)
            saved_storage_key = private_whatsapp_media_storage.save(storage_key, File(temporary_file))
        finally:
            temporary_file.close()

        locked_attachment.storage_key = saved_storage_key
        locked_attachment.storage_status = MEDIA_STORAGE_STATUS_REGISTRY.STORED
        locked_attachment.mime_type = mime_type
        locked_attachment.size = size
        locked_attachment.checksum = checksum
        locked_attachment.is_stored = True
        locked_attachment.stored_at = timezone.now()
        locked_attachment.storage_failed_at = None
        locked_attachment.storage_error = ""
        locked_attachment.save(update_fields=["storage_key", "storage_status", "mime_type", "size", "checksum", "is_stored", "stored_at", "storage_failed_at", "storage_error", "updated_at"])

        return locked_attachment

    except (MetaMediaError, ImproperlyConfigured) as error:
        locked_attachment.storage_status = MEDIA_STORAGE_STATUS_REGISTRY.FAILED
        locked_attachment.is_stored = False
        locked_attachment.storage_failed_at = timezone.now()
        locked_attachment.storage_error = str(error)
        locked_attachment.save(update_fields=["storage_status", "is_stored", "storage_failed_at", "storage_error", "updated_at"])
        raise
