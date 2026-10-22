import logging

from celery import shared_task
from django.db import DatabaseError

from whatsapp.meta.media import MetaMediaError
from whatsapp.models import MediaAttachment
from whatsapp.operations.media import store_media_attachment
from whatsapp.operations.webhooks import persist_whatsapp_webhook_payload


logger = logging.getLogger(__name__)


# Define your background tasks here.


def summarize_whatsapp_webhook_payload(payload):
    """
        DOCSTRING: Summarize WhatsApp Webhook Payload

        Description:
        - Extract safe diagnostic information from an authenticated
          Meta WhatsApp webhook payload.
        - Identify message, delivery-status, and WhatsApp Business App
          Coexistence events.

        Notes:
        - Customer phone numbers and message contents are excluded.
        - Authorization credentials are never included.
        - Meta WABA IDs and Phone Number IDs are retained for diagnostics.
        - Unsupported webhook fields are counted without exposing
          their complete payload.
    """

    summary = {
        "object": payload.get("object"),
        "entries": 0,
        "changes": 0,
        "messages": 0,
        "message_echoes": 0,
        "statuses": 0,
        "unknown_changes": 0,
        "events": [],
    }

    entries = payload.get("entry") or []

    for entry in entries:
        summary["entries"] += 1

        waba_id = entry.get("id")

        for change in entry.get("changes") or []:
            summary["changes"] += 1

            field = change.get("field")
            value = change.get("value") or {}
            metadata = value.get("metadata") or {}

            phone_number_id = metadata.get("phone_number_id")

            messages = value.get("messages") or []
            message_echoes = value.get("message_echoes") or []
            statuses = value.get("statuses") or []

            summary["messages"] += len(messages)
            summary["message_echoes"] += len(message_echoes)
            summary["statuses"] += len(statuses)

            if not messages and not message_echoes and not statuses:
                summary["unknown_changes"] += 1

            event_summary = {
                "waba_id": waba_id,
                "phone_number_id": phone_number_id,
                "field": field,
                "messages_count": len(messages),
                "message_echoes_count": len(message_echoes),
                "statuses_count": len(statuses),
                "message_types": [
                    message.get("type")
                    for message in messages
                    if isinstance(message, dict)
                ],
                "echo_types": [
                    message.get("type")
                    for message in message_echoes
                    if isinstance(message, dict)
                ],
                "delivery_statuses": [
                    status.get("status")
                    for status in statuses
                    if isinstance(status, dict)
                ],
            }

            summary["events"].append(event_summary)

    return summary


@shared_task(
    bind=True,
    autoretry_for=(DatabaseError,),
    max_retries=5,
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
    acks_late=True,
)
def process_whatsapp_webhook_task(self, payload):
    """
        DOCSTRING: Process WhatsApp Webhook Task

        Description:
        - Process a previously authenticated global Dialoqo Meta webhook
          payload in the background.
        - Resolve company ownership from the Meta Phone Number ID.
        - Persist WhatsApp messages and delivery-status events.
        - Generate structured diagnostics for WhatsApp Business App
          Coexistence testing.

        Notes:
        - Webhook signature verification occurs before this task is queued.
        - Sensitive Meta credentials are never passed through Redis.
        - Only the authenticated JSON payload is passed to Celery.
        - Message uniqueness provides retry-safe idempotency.
        - Database failures are automatically retried.
        - Delivery statuses arriving before outbound persistence are retried with bounded backoff.
        - Unresolved message identifiers are reported after the retry limit instead of disappearing silently.
        - Complete webhook payloads are never printed.
        - Diagnostic logs exclude customer phone numbers and content.
    """

    task_id = self.request.id
    retry_count = self.request.retries

    logger.info(
        "[WHATSAPP WEBHOOK] START | task_id=%s | retries=%s",
        task_id,
        retry_count,
    )

    summary = summarize_whatsapp_webhook_payload(
        payload=payload,
    )

    logger.info(
        "[WHATSAPP WEBHOOK] SUMMARY | "
        "task_id=%s | entries=%s | changes=%s | "
        "messages=%s | echoes=%s | statuses=%s",
        task_id,
        summary["entries"],
        summary["changes"],
        summary["messages"],
        summary["message_echoes"],
        summary["statuses"],
    )

    for event in summary["events"]:

        logger.info(
            "[WHATSAPP WEBHOOK] EVENT | "
            "task_id=%s | "
            "waba_id=%s | "
            "phone_number_id=%s | "
            "field=%s | "
            "messages=%s | "
            "echoes=%s | "
            "statuses=%s",
            task_id,
            event["waba_id"],
            event["phone_number_id"],
            event["field"],
            event["messages_count"],
            event["message_echoes_count"],
            event["statuses_count"],
        )

        if event["messages_count"]:

            logger.info(
                "[WHATSAPP WEBHOOK] INCOMING MESSAGES | "
                "task_id=%s | types=%s",
                task_id,
                event["message_types"],
            )

        if event["message_echoes_count"]:

            logger.info(
                "[WHATSAPP COEXISTENCE] BUSINESS APP ECHOES | "
                "task_id=%s | types=%s",
                task_id,
                event["echo_types"],
            )

        if event["statuses_count"]:

            logger.info(
                "[WHATSAPP WEBHOOK] DELIVERY STATUSES | "
                "task_id=%s | statuses=%s",
                task_id,
                event["delivery_statuses"],
            )

    try:

        result = persist_whatsapp_webhook_payload(
            payload=payload,
        )

    except DatabaseError:

        logger.exception(
            "[WHATSAPP WEBHOOK] DATABASE ERROR | "
            "task_id=%s | retry=%s",
            task_id,
            retry_count,
        )

        raise

    except Exception:

        logger.exception(
            "[WHATSAPP WEBHOOK] PROCESSING FAILED | "
            "task_id=%s",
            task_id,
        )

        raise

    unmatched_statuses = result.get("unmatched_statuses", 0)

    if unmatched_statuses:
        if retry_count < self.max_retries:
            logger.warning(
                "[WHATSAPP WEBHOOK] STATUS RETRY | task_id=%s | unmatched=%s | retry=%s",
                task_id,
                unmatched_statuses,
                retry_count,
            )
            raise self.retry(countdown=min(2 ** (retry_count + 1), 30))

        logger.error(
            "[WHATSAPP WEBHOOK] UNRESOLVED STATUSES | task_id=%s | unmatched=%s | retries=%s",
            task_id,
            unmatched_statuses,
            retry_count,
        )

    logger.info(
        "[WHATSAPP WEBHOOK] COMPLETED | "
        "task_id=%s | created=%s | updated=%s | ignored=%s",
        task_id,
        result.get("created", 0),
        result.get("updated", 0),
        result.get("ignored", 0),
    )

    if summary["message_echoes"]:

        logger.info(
            "[WHATSAPP COEXISTENCE] ECHO PROCESSING RESULT | "
            "task_id=%s | "
            "received_echoes=%s | "
            "total_created=%s | "
            "total_ignored=%s",
            task_id,
            summary["message_echoes"],
            result.get("created", 0),
            result.get("ignored", 0),
        )

    return result


@shared_task(
    bind=True,
    autoretry_for=(DatabaseError,),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
    acks_late=True,
)
def store_whatsapp_media_attachment_task(self, attachment_id):
    """
        DOCSTRING: Store WhatsApp Media Attachment Task

        Description:
        - Retrieve and persist the binary content of a WhatsApp media
          attachment asynchronously.
        - Reuse the existing private-media storage subsystem.
        - Generate diagnostics for media storage operations.

        Notes:
        - Only the attachment database identifier is sent to Celery.
        - Sensitive Meta credentials remain resolved internally.
        - Already stored attachments are treated idempotently.
        - Temporary Meta media failures are retried.
        - Missing attachment records are ignored safely.
    """

    task_id = self.request.id

    logger.info(
        "[WHATSAPP MEDIA] START | "
        "task_id=%s | attachment_id=%s",
        task_id,
        attachment_id,
    )

    attachment = MediaAttachment.objects.select_related(
        "message",
        "message__conversation",
        "message__conversation__whatsapp_number",
        "message__conversation__whatsapp_number__whatsapp_business_account",
    ).filter(
        id=attachment_id,
        is_active=True,
    ).first()

    if attachment is None:

        logger.warning(
            "[WHATSAPP MEDIA] ATTACHMENT NOT FOUND | "
            "task_id=%s | attachment_id=%s",
            task_id,
            attachment_id,
        )

        return {
            "stored": False,
            "ignored": True,
        }

    if attachment.is_stored and attachment.storage_key:

        logger.info(
            "[WHATSAPP MEDIA] ALREADY STORED | "
            "task_id=%s | attachment_id=%s",
            task_id,
            attachment.id,
        )

        return {
            "stored": True,
            "ignored": True,
        }

    try:

        attachment = store_media_attachment(
            attachment,
        )

    except MetaMediaError as error:

        logger.warning(
            "[WHATSAPP MEDIA] RETRIEVAL FAILED | "
            "task_id=%s | attachment_id=%s | error_type=%s",
            task_id,
            attachment.id,
            type(error).__name__,
        )

        raise self.retry(
            exc=error,
            countdown=min(
                2 ** self.request.retries,
                120,
            ),
            max_retries=5,
        )

    logger.info(
        "[WHATSAPP MEDIA] STORED SUCCESSFULLY | "
        "task_id=%s | attachment_id=%s",
        task_id,
        attachment.id,
    )

    return {
        "stored": True,
        "ignored": False,
        "attachment_id": attachment.id,
    }
