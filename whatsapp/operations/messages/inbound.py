"""Inbound and Coexistence message persistence."""

from django.db import IntegrityError, transaction
from django.utils import timezone

from whatsapp.models import Conversation, Customer, Message
from whatsapp.operations.conversations import (
    mark_conversation_as_unread,
    update_conversation_last_message,
)
from whatsapp.operations.media import persist_media_attachment
from whatsapp.operations.messages.lifecycle import edit_message, revoke_message
from whatsapp.operations.monitoring import is_message_inside_monitoring_window
from whatsapp.realtime.events import (
    schedule_conversation_updated_event,
    schedule_message_created_event,
)
from whatsapp.registry import MESSAGE_DIRECTION_REGISTRY, MESSAGE_STATUS_REGISTRY
from whatsapp.utils.webhooks import (
    extract_context_message_id,
    extract_edit_event,
    extract_media_payload,
    extract_message_content,
    extract_message_text,
    extract_revocation_event,
    parse_meta_timestamp,
    resolve_customer_phone_number,
    resolve_message_direction,
    resolve_message_type,
)


@transaction.atomic
def resolve_customer(
    whatsapp_number,
    phone_number,
    profile_name="",
):
    """
        DOCSTRING: Resolve Customer

        Description:
        - Resolve or create the company-scoped external WhatsApp participant associated with a monitored message.
        - Synchronize the customer's latest available WhatsApp profile name.

        Notes:
        - Customer phone numbers remain unique within each company.
        - Customer records remain monitoring identities and not CRM records.
    """

    customer, created = Customer.objects.get_or_create(
        company=whatsapp_number.company,
        phone_number=phone_number,
        defaults={
            "profile_name": profile_name,
        },
    )

    if (
        not created
        and profile_name
        and customer.profile_name != profile_name
    ):
        customer.profile_name = profile_name
        customer.save(
            update_fields=[
                "profile_name",
                "updated_at",
            ]
        )

    return customer


@transaction.atomic
def resolve_message_context(message):
    """
        DOCSTRING: Resolve Message Context

        Description:
        - Link a message to its referenced local message when the corresponding Meta message identifier has already been persisted.

        Notes:
        - Missing referenced messages do not invalidate the current message.
        - meta_context_message_id remains stored so the relationship can be resolved later.
    """

    if not message.meta_context_message_id:
        return message

    context_message = Message.objects.filter(
        meta_message_id=message.meta_context_message_id,
        conversation=message.conversation,
        is_active=True,
    ).first()

    if context_message is None:
        return message

    message.context_message = context_message

    message.save(
        update_fields=[
            "context_message",
            "updated_at",
        ]
    )

    return message


@transaction.atomic
def resolve_pending_message_contexts(message):
    """
        DOCSTRING: Resolve Pending Message Contexts

        Description:
        - Resolve previously persisted messages that referenced the newly persisted message before it existed locally.

        Notes:
        - Only unresolved context relationships within the same conversation are updated.
        - This supports webhook events delivered out of chronological order.
    """

    Message.objects.filter(
        conversation=message.conversation,
        meta_context_message_id=message.meta_message_id,
        context_message__isnull=True,
        is_active=True,
    ).update(
        context_message=message,
        updated_at=timezone.now(),
    )


@transaction.atomic
def persist_whatsapp_single_message(
    whatsapp_number,
    metadata,
    contacts,
    contact_map,
    message_data,
):
    """
        DOCSTRING: Persist WhatsApp Single Message

        Description:
        - Process one WhatsApp message object from a validated webhook change.
        - Route edit and revocation events or persist a new normalized inbound or outbound message.
        - Update read-state counters only when the persisted message represents relevant inbound conversation content.

        Notes:
        - New message persistence is restricted to the active monitoring window.
        - Edits and revocations may update already persisted monitored messages after monitoring has stopped.
        - Synchronized outbound messages from WhatsApp Business App Coexistence are persisted as OUTBOUND.
        - Duplicate Meta message identifiers are concurrency-safe and idempotent.
        - Webhook-ingested messages are operational synchronization data and are not recorded as user audit events.
    """

    message_timestamp = parse_meta_timestamp(
        message_data.get("timestamp")
    )

    if message_timestamp is None:
        return {
            "created": 0,
            "updated": 0,
            "ignored": 1,
        }

    edit_event = extract_edit_event(
        message_data
    )

    if edit_event:
        original_message_id = edit_event["original_message_id"]
        edited_message_data = edit_event["message_data"]

        if not original_message_id:
            return {
                "created": 0,
                "updated": 0,
                "ignored": 1,
            }

        meta_message_type = (
            edited_message_data.get("type")
            or "text"
        )

        message_type = resolve_message_type(
            meta_message_type
        )

        text_body = extract_message_text(
            message_data=edited_message_data,
            message_type=message_type,
        )

        content_data = extract_message_content(
            message_data=edited_message_data,
            message_type=message_type,
            meta_message_type=meta_message_type,
        )

        message, changed = edit_message(
            meta_message_id=original_message_id,
            text_body=text_body,
            content_data=content_data,
            message_type=message_type,
            edited_at=message_timestamp,
        )

        if message is None:
            return {
                "created": 0,
                "updated": 0,
                "ignored": 1,
            }

        media_payload = extract_media_payload(
            message_data=edited_message_data,
            message_type=message_type,
        )

        if media_payload:
            persist_media_attachment(
                message=message,
                media_payload=media_payload,
            )

        return {
            "created": 0,
            "updated": 1 if changed else 0,
            "ignored": 0 if changed else 1,
        }

    revocation_event = extract_revocation_event(
        message_data
    )

    if revocation_event:
        original_message_id = revocation_event[
            "original_message_id"
        ]

        if not original_message_id:
            return {
                "created": 0,
                "updated": 0,
                                "ignored": 1,
            }

        message, changed = revoke_message(
            meta_message_id=original_message_id,
            revoked_at=message_timestamp,
        )

        if message is None:
            return {
                "created": 0,
                "updated": 0,
                "ignored": 1,
            }

        return {
            "created": 0,
            "updated": 1 if changed else 0,
            "ignored": 0 if changed else 1,
        }

    meta_message_id = message_data.get("id")
    meta_message_type = message_data.get("type")

    if not meta_message_id or not meta_message_type:
        return {
            "created": 0,
            "updated": 0,
            "ignored": 1,
        }

    existing_message = Message.objects.filter(
        meta_message_id=meta_message_id
    ).first()

    if existing_message:
        return {
            "created": 0,
            "updated": 0,
            "ignored": 1,
        }

    if not is_message_inside_monitoring_window(
        whatsapp_number=whatsapp_number,
        message_timestamp=message_timestamp,
    ):
        return {
            "created": 0,
            "updated": 0,
            "ignored": 1,
        }

    direction = resolve_message_direction(
        message_data=message_data,
        whatsapp_number=whatsapp_number,
        metadata=metadata,
    )

    customer_phone_number = resolve_customer_phone_number(
        message_data=message_data,
        direction=direction,
        whatsapp_number=whatsapp_number,
        metadata=metadata,
        contacts=contacts,
    )

    if not customer_phone_number:
        return {
            "created": 0,
            "updated": 0,
            "ignored": 1,
        }

    contact_data = contact_map.get(
        customer_phone_number,
        {},
    )

    profile_data = contact_data.get("profile") or {}
    profile_name = profile_data.get("name") or ""

    customer = resolve_customer(
        whatsapp_number=whatsapp_number,
        phone_number=customer_phone_number,
        profile_name=profile_name,
    )

    conversation, _ = Conversation.objects.get_or_create(
        whatsapp_number=whatsapp_number,
        customer=customer,
    )

    message_type = resolve_message_type(
        meta_message_type
    )

    text_body = extract_message_text(
        message_data=message_data,
        message_type=message_type,
    )

    content_data = extract_message_content(
        message_data=message_data,
        message_type=message_type,
        meta_message_type=meta_message_type,
    )

    media_payload = extract_media_payload(
        message_data=message_data,
        message_type=message_type,
    )

    meta_context_message_id = extract_context_message_id(
        message_data
    )

    sender_phone_number = (
        customer_phone_number
        if direction == MESSAGE_DIRECTION_REGISTRY.INBOUND
        else whatsapp_number.phone_number
    )

    recipient_phone_number = (
        whatsapp_number.phone_number
        if direction == MESSAGE_DIRECTION_REGISTRY.INBOUND
        else customer_phone_number
    )

    status = (
        MESSAGE_STATUS_REGISTRY.RECEIVED
        if direction == MESSAGE_DIRECTION_REGISTRY.INBOUND
        else MESSAGE_STATUS_REGISTRY.SENT
    )

    try:
        with transaction.atomic():
            message = Message.objects.create(
                conversation=conversation,
                meta_message_id=meta_message_id,
                direction=direction,
                message_type=message_type,
                status=status,
                sender_phone_number=sender_phone_number,
                recipient_phone_number=recipient_phone_number,
                text_body=text_body,
                content_data=content_data,
                meta_context_message_id=meta_context_message_id,
                message_timestamp=message_timestamp,
                meta_event_timestamp=timezone.now(),
                status_updated_at=message_timestamp,
                sent_at=(
                    message_timestamp
                    if direction == MESSAGE_DIRECTION_REGISTRY.OUTBOUND
                    else None
                ),
            )

    except IntegrityError:
        return {
            "created": 0,
            "updated": 0,
            "ignored": 1,
        }

    resolve_message_context(
        message
    )

    resolve_pending_message_contexts(
        message
    )

    persist_media_attachment(
        message=message,
        media_payload=media_payload,
    )

    update_conversation_last_message(
        conversation=conversation,
        message=message,
    )

    mark_conversation_as_unread(
        conversation=conversation,
        message=message,
    )

    schedule_message_created_event(
        message=message,
    )

    schedule_conversation_updated_event(
        conversation=conversation,
    )

    return {
        "created": 1,
        "updated": 0,
        "ignored": 0,
    }
