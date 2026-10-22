"""Message delivery, editing, and revocation workflows."""

from django.db import transaction

from whatsapp.models import Message
from whatsapp.realtime.events import (
    schedule_conversation_updated_event,
    schedule_message_status_changed_event,
    schedule_message_updated_event,
)
from whatsapp.registry import MESSAGE_DIRECTION_REGISTRY, MESSAGE_STATUS_REGISTRY


@transaction.atomic
def apply_message_status(
    meta_message_id,
    status,
    status_timestamp,
    failure_code="",
    failure_message="",
):
    """
        DOCSTRING: Apply Message Status

        Description:
        - Apply a delivery lifecycle update to an existing outbound WhatsApp message.
        - Prevent delayed webhook events from regressing a more advanced delivery state.

        Notes:
        - RECEIVED is reserved for inbound messages.
        - PENDING, SENT, DELIVERED, and READ represent progressive outbound delivery states.
        - FAILED is accepted unless the message already reached DELIVERED or READ.
        - Duplicate status notifications are idempotent.
        - Automatic Meta delivery-status transitions are operational state and are not recorded as human audit events.
    """

    message = Message.objects.select_for_update().filter(
        meta_message_id=meta_message_id,
        is_active=True,
    ).first()

    if message is None:
        return None, False

    if message.direction != MESSAGE_DIRECTION_REGISTRY.OUTBOUND:
        return message, False

    status_rank = {
        MESSAGE_STATUS_REGISTRY.PENDING: 0,
        MESSAGE_STATUS_REGISTRY.SENT: 1,
        MESSAGE_STATUS_REGISTRY.DELIVERED: 2,
        MESSAGE_STATUS_REGISTRY.READ: 3,
    }

    if status == MESSAGE_STATUS_REGISTRY.RECEIVED:
        return message, False

    if status == MESSAGE_STATUS_REGISTRY.FAILED:
        if message.status in [
            MESSAGE_STATUS_REGISTRY.DELIVERED,
            MESSAGE_STATUS_REGISTRY.READ,
        ]:
            return message, False

        if (
            message.status == MESSAGE_STATUS_REGISTRY.FAILED
            and message.status_updated_at
            and status_timestamp <= message.status_updated_at
        ):
            return message, False

        message.status = MESSAGE_STATUS_REGISTRY.FAILED
        message.status_updated_at = status_timestamp
        message.failed_at = status_timestamp
        message.failure_code = str(failure_code or "")
        message.failure_message = failure_message or ""

        message.save(
            update_fields=[
                "status",
                "status_updated_at",
                "failed_at",
                "failure_code",
                "failure_message",
                "updated_at",
            ]
        )

        schedule_message_status_changed_event(
            message=message,
        )

        return message, True

    if status not in status_rank:
        return message, False

    current_rank = status_rank.get(
        message.status,
        -1,
    )

    incoming_rank = status_rank[status]

    if message.status == MESSAGE_STATUS_REGISTRY.FAILED:
        return message, False

    if incoming_rank < current_rank:
        return message, False

    if (
        incoming_rank == current_rank
        and message.status_updated_at
        and status_timestamp <= message.status_updated_at
    ):
        return message, False

    message.status = status
    message.status_updated_at = status_timestamp

    if (
        status == MESSAGE_STATUS_REGISTRY.SENT
        and message.sent_at is None
    ):
        message.sent_at = status_timestamp

    if status == MESSAGE_STATUS_REGISTRY.DELIVERED:
        if message.sent_at is None:
            message.sent_at = status_timestamp

        if message.delivered_at is None:
            message.delivered_at = status_timestamp

    if status == MESSAGE_STATUS_REGISTRY.READ:
        if message.sent_at is None:
            message.sent_at = status_timestamp

        if message.delivered_at is None:
            message.delivered_at = status_timestamp

        if message.read_at is None:
            message.read_at = status_timestamp

    message.save(
        update_fields=[
            "status",
            "status_updated_at",
            "sent_at",
            "delivered_at",
            "read_at",
            "updated_at",
        ]
    )

    schedule_message_status_changed_event(
        message=message,
    )

    return message, True


@transaction.atomic
def edit_message(
    meta_message_id,
    text_body,
    content_data,
    message_type,
    edited_at,
):
    """
        DOCSTRING: Edit Message

        Description:
        - Replace the currently visible representation of an existing WhatsApp message with its edited content.
        - Preserve the original text the first time the message is edited.

        Notes:
        - Message identity and conversation position remain unchanged.
        - Revoked messages cannot be made visible again by delayed edit events.
        - Older or duplicate edit events are ignored.
        - Editing a message does not increment unread counters.
        - Meta-originated edit synchronization is not recorded as a human audit action.
    """

    message = Message.objects.select_for_update().filter(
        meta_message_id=meta_message_id,
        is_active=True,
    ).first()

    if message is None:
        return None, False

    if message.revoked_at:
        return message, False

    if message.edited_at and edited_at <= message.edited_at:
        return message, False

    if not message.original_text_body:
        message.original_text_body = message.text_body

    message.text_body = text_body or ""
    message.content_data = content_data or {}
    message.message_type = message_type
    message.edited_at = edited_at

    message.save(
        update_fields=[
            "original_text_body",
            "text_body",
            "content_data",
            "message_type",
            "edited_at",
            "updated_at",
        ]
    )

    schedule_message_updated_event(
        message=message,
    )

    schedule_conversation_updated_event(
        conversation=message.conversation,
    )

    return message, True


@transaction.atomic
def revoke_message(meta_message_id, revoked_at):
    """
        DOCSTRING: Revoke Message

        Description:
        - Mark an existing monitored message as revoked without deleting its database record.

        Notes:
        - The original stored record remains available internally for traceability.
        - Serializers hide revoked text and structured content from monitoring responses.
        - Revocation is irreversible inside Dialoqo.
        - Revocation does not increment unread counters.
        - Meta-originated revocation synchronization is not recorded as a human audit action.
    """

    message = Message.objects.select_for_update().filter(
        meta_message_id=meta_message_id,
        is_active=True,
    ).first()

    if message is None:
        return None, False

    if message.revoked_at:
        return message, False

    message.revoked_at = revoked_at

    message.save(
        update_fields=[
            "revoked_at",
            "updated_at",
        ]
    )

    schedule_message_updated_event(
        message=message,
    )

    schedule_conversation_updated_event(
        conversation=message.conversation,
    )

    return message, True
