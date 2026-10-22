"""Webhook event dispatch and processing summaries."""

import logging

from django.db import transaction

from whatsapp.operations.messages.inbound import persist_whatsapp_single_message
from whatsapp.operations.messages.lifecycle import apply_message_status, revoke_message
from whatsapp.operations.monitoring import resolve_whatsapp_number
from whatsapp.registry import MESSAGE_STATUS_REGISTRY
from whatsapp.utils.webhooks import extract_failure_details, parse_meta_timestamp


logger = logging.getLogger(__name__)


@transaction.atomic
def persist_whatsapp_message_event(value):
    """
        DOCSTRING: Persist WhatsApp Message Event

        Description:
        - Process all standard message objects contained inside one validated WhatsApp webhook change.
        - Resolve the corresponding corporate WhatsApp number and route each message independently.

        Notes:
        - Standard WhatsApp messages are contained in value.messages.
        - Unknown and unsupported message types are persisted using the UNSUPPORTED registry value.
        - Invalid events are ignored individually without rejecting the entire webhook request.
        - Tenant ownership is resolved from metadata.phone_number_id.
    """

    metadata = value.get("metadata") or {}
    contacts = value.get("contacts") or []
    messages = value.get("messages") or []
    meta_phone_number_id = metadata.get("phone_number_id")

    if not messages:
        return {
            "created": 0,
            "updated": 0,
            "ignored": 0,
        }

    whatsapp_number = resolve_whatsapp_number(
        meta_phone_number_id=meta_phone_number_id,
    )

    if whatsapp_number is None:
        return {
            "created": 0,
            "updated": 0,
            "ignored": len(messages),
        }

    contact_map = {
        contact.get("wa_id"): contact
        for contact in contacts
        if contact.get("wa_id")
    }

    created_count = 0
    updated_count = 0
    ignored_count = 0

    for message_data in messages:
        result = persist_whatsapp_single_message(
            whatsapp_number=whatsapp_number,
            metadata=metadata,
            contacts=contacts,
            contact_map=contact_map,
            message_data=message_data,
        )

        created_count += result["created"]
        updated_count += result["updated"]
        ignored_count += result["ignored"]

    return {
        "created": created_count,
        "updated": updated_count,
        "ignored": ignored_count,
    }


@transaction.atomic
def persist_whatsapp_message_echo_event(value):
    """
        DOCSTRING: Persist WhatsApp Message Echo Event

        Description:
        - Process outbound WhatsApp messages synchronized from the WhatsApp Business App.
        - Reuse the canonical Dialoqo message-ingestion pipeline for Coexistence message echoes.
        - Resolve tenant ownership from the Meta Phone Number ID contained in webhook metadata.

        Notes:
        - Meta delivers WhatsApp Business App outbound synchronization through
          the smb_message_echoes webhook field.
        - Message objects are contained in value.message_echoes rather than value.messages.
        - Echo messages retain Meta's authoritative message identifier for idempotency
          and subsequent delivery-status reconciliation.
        - Direction and customer identity are resolved by the canonical message helpers.
        - Messages synchronized from the WhatsApp Business App are operational events
          and do not create Dialoqo user SEND audit events.
    """

    metadata = value.get("metadata") or {}
    contacts = value.get("contacts") or []
    message_echoes = value.get("message_echoes") or []
    meta_phone_number_id = metadata.get("phone_number_id")

    if not message_echoes:
        return {
            "created": 0,
            "updated": 0,
            "ignored": 0,
        }

    whatsapp_number = resolve_whatsapp_number(
        meta_phone_number_id=meta_phone_number_id,
    )

    if whatsapp_number is None:
        return {
            "created": 0,
            "updated": 0,
            "ignored": len(message_echoes),
        }

    contact_map = {
        contact.get("wa_id"): contact
        for contact in contacts
        if contact.get("wa_id")
    }

    created_count = 0
    updated_count = 0
    ignored_count = 0

    for message_data in message_echoes:
        result = persist_whatsapp_single_message(
            whatsapp_number=whatsapp_number,
            metadata=metadata,
            contacts=contacts,
            contact_map=contact_map,
            message_data=message_data,
        )

        created_count += result["created"]
        updated_count += result["updated"]
        ignored_count += result["ignored"]

    return {
        "created": created_count,
        "updated": updated_count,
        "ignored": ignored_count,
    }


@transaction.atomic
def persist_whatsapp_status_event(value):
    """
        DOCSTRING: Persist WhatsApp Status Event

        Description:
        - Process WhatsApp outbound delivery-status notifications.
        - Normalize Meta statuses and apply them to already persisted outbound messages.

        Notes:
        - Supported delivery statuses are sent, delivered, read, and failed.
        - Duplicate and out-of-order notifications must not regress the current state.
        - Unknown statuses are ignored safely.
        - Delivery statuses never affect Dialoqo monitoring-user unread counters.
        - Coexistence message echoes use the same authoritative Meta message ID,
          allowing their later status events to reuse this lifecycle operation.
        - Unmatched statuses for monitored numbers are counted so the worker can retry after outbound persistence.
    """

    statuses = value.get("statuses") or []
    updated_count = 0
    ignored_count = 0
    unmatched_statuses = 0

    status_mapping = {
        "sent": MESSAGE_STATUS_REGISTRY.SENT,
        "delivered": MESSAGE_STATUS_REGISTRY.DELIVERED,
        "read": MESSAGE_STATUS_REGISTRY.READ,
        "failed": MESSAGE_STATUS_REGISTRY.FAILED,
    }

    for status_data in statuses:
        meta_message_id = status_data.get("id")
        meta_status = status_data.get("status")
        status_timestamp = parse_meta_timestamp(
            status_data.get("timestamp")
        )

        if (
            not meta_message_id
            or not meta_status
            or status_timestamp is None
        ):
            ignored_count += 1
            continue

        if meta_status in [
            "deleted",
            "revoked",
        ]:
            message, changed = revoke_message(
                meta_message_id=meta_message_id,
                revoked_at=status_timestamp,
            )

            if message is None or not changed:
                ignored_count += 1
            else:
                updated_count += 1

            continue

        normalized_status = status_mapping.get(
            meta_status
        )

        if normalized_status is None:
            ignored_count += 1
            continue

        failure_code, failure_message = extract_failure_details(
            status_data
        )

        message, changed = apply_message_status(
            meta_message_id=meta_message_id,
            status=normalized_status,
            status_timestamp=status_timestamp,
            failure_code=failure_code,
            failure_message=failure_message,
        )

        if message is None:
            metadata = value.get("metadata") or {}
            whatsapp_number = resolve_whatsapp_number(metadata.get("phone_number_id"))

            if whatsapp_number and whatsapp_number.is_monitoring_enabled:
                unmatched_statuses += 1
                logger.info(
                    "[WHATSAPP STATUS] AWAITING MESSAGE | meta_message_id=%s | status=%s | number_id=%s",
                    meta_message_id,
                    normalized_status,
                    whatsapp_number.id,
                )

            ignored_count += 1
        elif not changed:
            ignored_count += 1
        else:
            updated_count += 1
            logger.info(
                "[WHATSAPP STATUS] APPLIED | meta_message_id=%s | status=%s | failure_code=%s",
                meta_message_id,
                normalized_status,
                failure_code,
            )

    result = {
        "created": 0,
        "updated": updated_count,
        "ignored": ignored_count,
    }
    if unmatched_statuses:
        result["unmatched_statuses"] = unmatched_statuses

    return result


def persist_whatsapp_webhook_payload(payload):
    """
        DOCSTRING: Persist WhatsApp Webhook Payload

        Description:
        - Process a complete validated Meta WhatsApp webhook payload.
        - Route standard messages, WhatsApp Business App Coexistence message echoes,
          and delivery-status events through the appropriate persistence operations.
        - Aggregate processing results for diagnostics.

        Notes:
        - Meta may deliver multiple entries and multiple changes in one request.
        - Standard message events are delivered through the messages webhook field.
        - WhatsApp Business App outbound synchronization is delivered through
          the smb_message_echoes webhook field.
        - Unknown webhook fields are ignored safely.
        - A malformed individual message or status does not prevent other events
          from being processed.
        - Message uniqueness and lifecycle guards provide webhook idempotency.
        - Unmatched delivery-status counts are propagated for bounded worker retries.
    """

    created_count = 0
    updated_count = 0
    ignored_count = 0
    unmatched_statuses = 0

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            field = change.get("field")
            value = change.get("value") or {}

            if field == "messages":
                processed_event = False

                if value.get("messages"):
                    result = persist_whatsapp_message_event(
                        value=value,
                    )

                    created_count += result["created"]
                    updated_count += result["updated"]
                    ignored_count += result["ignored"]

                    processed_event = True

                if value.get("statuses"):
                    result = persist_whatsapp_status_event(
                        value=value,
                    )

                    created_count += result["created"]
                    updated_count += result["updated"]
                    ignored_count += result["ignored"]
                    unmatched_statuses += result.get("unmatched_statuses", 0)

                    processed_event = True

                if not processed_event:
                    ignored_count += 1

                continue

            if field == "smb_message_echoes":
                result = persist_whatsapp_message_echo_event(
                    value=value,
                )

                created_count += result["created"]
                updated_count += result["updated"]
                ignored_count += result["ignored"]

                continue

    result = {
        "created": created_count,
        "updated": updated_count,
        "ignored": ignored_count,
    }
    if unmatched_statuses:
        result["unmatched_statuses"] = unmatched_statuses

    return result
