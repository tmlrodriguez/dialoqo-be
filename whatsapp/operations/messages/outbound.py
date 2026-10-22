"""Outbound text-message validation, sending, and persistence."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from auditing.operations import schedule_audit_event
from auditing.registry import AUDIT_ACTION_REGISTRY, AUDIT_CATEGORY_REGISTRY

from whatsapp.meta.messages import send_meta_whatsapp_text_message
from whatsapp.models import Conversation, Message
from whatsapp.operations.assignments import validate_member_whatsapp_number_operation
from whatsapp.operations.conversations import update_conversation_last_message
from whatsapp.realtime.events import (
    schedule_conversation_updated_event,
    schedule_message_created_event,
)
from whatsapp.registry import (
    MESSAGE_DIRECTION_REGISTRY,
    MESSAGE_STATUS_REGISTRY,
    MESSAGE_TYPE_REGISTRY,
)


def validate_outbound_whatsapp_conversation(conversation):
    """
        DOCSTRING: Validate Outbound WhatsApp Conversation

        Description:
        - Validate the complete tenant chain required to send an outbound WhatsApp message.

        Notes:
        - Conversation, customer, WhatsApp number, branch, WABA, and company must form one consistent tenant.
        - Cross-company relationships are rejected even if corrupted data somehow exists.
    """

    whatsapp_number = conversation.whatsapp_number
    customer = conversation.customer
    company = whatsapp_number.company
    branch = whatsapp_number.branch
    whatsapp_business_account = (
        whatsapp_number.whatsapp_business_account
    )

    if not conversation.is_active:
        raise ValidationError({
            "conversation": "Envío de mensaje rechazado: la conversación se encuentra inactiva."
        })

    if customer.company_id != company.id:
        raise ValidationError({
            "customer": "Envío de mensaje rechazado: el cliente no pertenece a la empresa del número de WhatsApp."
        })

    if branch.company_id != company.id:
        raise ValidationError({
            "branch": "Envío de mensaje rechazado: la sucursal no pertenece a la empresa del número de WhatsApp."
        })

    if whatsapp_business_account.company_id != company.id:
        raise ValidationError({
            "whatsapp_business_account": "Envío de mensaje rechazado: la cuenta de WhatsApp Business no pertenece a la empresa."
        })

    if not customer.is_active:
        raise ValidationError({
            "customer": "Envío de mensaje rechazado: el cliente se encuentra inactivo."
        })

    if not company.is_active:
        raise ValidationError({
            "company": "Envío de mensaje rechazado: la empresa se encuentra inactiva."
        })

    if not branch.is_active:
        raise ValidationError({
            "branch": "Envío de mensaje rechazado: la sucursal se encuentra inactiva."
        })

    if not whatsapp_number.is_active:
        raise ValidationError({
            "whatsapp_number": "Envío de mensaje rechazado: el número de WhatsApp se encuentra inactivo."
        })

    if not whatsapp_number.is_connected:
        raise ValidationError({
            "whatsapp_number": "Envío de mensaje rechazado: el número de WhatsApp no se encuentra conectado."
        })

    if not whatsapp_number.is_monitoring_enabled:
        raise ValidationError({
            "whatsapp_number": "Envío de mensaje rechazado: el monitoreo no se encuentra activo."
        })

    if not whatsapp_business_account.is_active:
        raise ValidationError({
            "whatsapp_business_account": "Envío de mensaje rechazado: la cuenta de WhatsApp Business se encuentra inactiva."
        })

    if not whatsapp_business_account.is_connected:
        raise ValidationError({
            "whatsapp_business_account": "Envío de mensaje rechazado: la cuenta de WhatsApp Business no se encuentra conectada."
        })

    if not whatsapp_business_account.is_webhook_configured:
        raise ValidationError({
            "whatsapp_business_account": "Envío de mensaje rechazado: el webhook no se encuentra configurado."
        })

    return conversation


@transaction.atomic
def persist_outbound_whatsapp_message(
    conversation,
    meta_message_id,
    text_body,
    actor,
    message_timestamp=None,
):
    """
        DOCSTRING: Persist Outbound WhatsApp Message

        Description:
        - Persist an outbound WhatsApp text message accepted by Meta.
        - Register the message using Meta's message identifier.
        - Update the corresponding conversation preview.
        - Record the user-initiated send operation in the centralized audit subsystem.

        Notes:
        - The operation is idempotent through Message.meta_message_id.
        - Newly accepted outbound messages are stored as PENDING until a Meta delivery-status webhook confirms SENT, DELIVERED, READ, or FAILED.
        - Outbound messages never increment monitoring-user unread counters.
        - message_timestamp represents the Dialoqo send time because Meta's send response does not provide the final delivery timestamp.
        - The actor is persisted through the author fields for traceability.
        - WhatsApp message text is intentionally excluded from audit metadata.
        - Duplicate persistence does not create a duplicate SEND audit event.
    """

    if message_timestamp is None:
        message_timestamp = timezone.now()

    locked_conversation = Conversation.objects.select_for_update().select_related(
        "customer",
        "whatsapp_number",
        "whatsapp_number__company",
        "whatsapp_number__branch",
    ).get(
        id=conversation.id
    )

    existing_message = Message.objects.filter(
        meta_message_id=meta_message_id
    ).first()

    if existing_message:
        return existing_message, False

    try:
        message = Message.objects.create(
            conversation=locked_conversation,
            meta_message_id=meta_message_id,
            direction=MESSAGE_DIRECTION_REGISTRY.OUTBOUND,
            message_type=MESSAGE_TYPE_REGISTRY.TEXT,
            status=MESSAGE_STATUS_REGISTRY.PENDING,
            sender_phone_number=locked_conversation.whatsapp_number.phone_number,
            recipient_phone_number=locked_conversation.customer.phone_number,
            text_body=text_body,
            original_text_body="",
            content_data={},
            message_timestamp=message_timestamp,
            meta_event_timestamp=message_timestamp,
            status_updated_at=message_timestamp,
            created_by=actor,
            updated_by=actor,
        )

    except IntegrityError:
        message = Message.objects.get(
            meta_message_id=meta_message_id
        )

        return message, False

        update_conversation_last_message(
        conversation=locked_conversation,
        message=message,
    )

    schedule_message_created_event(
        message=message,
    )

    schedule_conversation_updated_event(
        conversation=locked_conversation,
    )

    schedule_audit_event(
        category=AUDIT_CATEGORY_REGISTRY.WHATSAPP,
        action=AUDIT_ACTION_REGISTRY.SEND,
        description="Mensaje de WhatsApp enviado desde Dialoqo.",
        actor=actor,
        company=locked_conversation.whatsapp_number.company,
        branch=locked_conversation.whatsapp_number.branch,
        target=message,
        metadata={
            "message_id": message.id,
            "meta_message_id": message.meta_message_id,
            "conversation_id": locked_conversation.id,
            "whatsapp_number_id": locked_conversation.whatsapp_number_id,
            "customer_id": locked_conversation.customer_id,
            "message_type": message.message_type,
            "direction": message.direction,
        },
    )

    return message, True


def send_outbound_whatsapp_text_message(
    conversation,
    text_body,
    actor,
):
    """
        DOCSTRING: Send Outbound WhatsApp Text Message

        Description:
        - Execute the complete Dialoqo outbound plain-text WhatsApp message workflow.
        - Validate the monitored conversation.
        - Send the message through Meta WhatsApp Cloud API.
        - Persist Meta's message identifier and the local outbound message representation.

        Notes:
        - Meta communication is performed before local persistence because Meta assigns the authoritative message identifier.
        - Successful Meta acceptance creates a local PENDING message.
        - Later Meta status webhooks reconcile the lifecycle to SENT, DELIVERED, READ, or FAILED.
        - Meta API failures do not create a fake WhatsApp message because no authoritative Meta message identifier exists.
        - Tenant and resource relationships are validated before any Meta API call is performed.
        - Successful local persistence creates the corresponding SEND audit event.
        - Message body content is never copied into auditing metadata.
    """

    conversation = Conversation.objects.select_related(
        "customer",
        "customer__company",
        "whatsapp_number",
        "whatsapp_number__company",
        "whatsapp_number__branch",
        "whatsapp_number__whatsapp_business_account",
    ).get(
        id=conversation.id
    )

    validate_member_whatsapp_number_operation(
        whatsapp_number=conversation.whatsapp_number,
        actor=actor,
    )

    validate_outbound_whatsapp_conversation(
        conversation=conversation,
    )

    normalized_text_body = str(
        text_body or ""
    ).strip()

    if not normalized_text_body:
        raise ValidationError({
            "text_body": "Envío de mensaje rechazado: el contenido del mensaje no puede estar vacío."
        })

    meta_result = send_meta_whatsapp_text_message(
        whatsapp_number=conversation.whatsapp_number,
        recipient_phone_number=conversation.customer.phone_number,
        text_body=normalized_text_body,
    )

    message, created = persist_outbound_whatsapp_message(
        conversation=conversation,
        meta_message_id=meta_result["meta_message_id"],
        text_body=normalized_text_body,
        actor=actor,
    )

    return {
        "message": message,
        "created": created,
    }
