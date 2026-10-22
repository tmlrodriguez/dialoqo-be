"""Meta Cloud API text and template message requests."""

import logging

from whatsapp.meta.client import MetaWhatsAppAPIError, meta_post
from whatsapp.utils.webhooks import normalize_phone_number


logger = logging.getLogger(__name__)


def send_meta_whatsapp_text_message(
    whatsapp_number,
    recipient_phone_number,
    text_body,
):
    """
        DOCSTRING: Send Meta WhatsApp Text Message

        Description:
        - Send a plain-text WhatsApp message through Meta Cloud API.
        - Resolve authorization through the owning WABA.

        Notes:
        - Business validation and persistence belong to the
          operation layer.
        - Recipient phone numbers are normalized.
        - Meta provides the authoritative message identifier.
    """

    normalized_recipient = normalize_phone_number(
        recipient_phone_number
    )

    if not normalized_recipient:
        raise MetaWhatsAppAPIError(
            error_message=(
                "Envío de mensaje rechazado: "
                "el número de destino no es válido."
            )
        )

    response_data = meta_post(
        resource_path=(
            f"{whatsapp_number.meta_phone_number_id}/messages"
        ),
        whatsapp_business_account=(
            whatsapp_number.whatsapp_business_account
        ),
        payload={
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalized_recipient,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": text_body,
            },
        },
    )

    messages = response_data.get("messages") or []

    if not messages or not messages[0].get("id"):
        raise MetaWhatsAppAPIError(
            error_message=(
                "Envío de mensaje rechazado: "
                "Meta no devolvió un identificador "
                "de mensaje válido."
            )
        )

    return {
        "meta_message_id": messages[0]["id"],
        "recipient_phone_number": normalized_recipient,
        "response_data": response_data,
    }


def send_meta_whatsapp_template_message(
    whatsapp_number,
    recipient_phone_number,
    template_name,
    language,
    components=None,
):
    """
        DOCSTRING: Send Meta WhatsApp Template Message

        Description:
        - Send an approved WhatsApp template message through
          Meta WhatsApp Cloud API.
        - Resolve authorization through the owning WABA.

        Notes:
        - Recipient phone numbers are normalized.
        - Template parameters use Meta component format.
        - Meta returns the authoritative message identifier.
        - Submission status is preserved independently of later delivery-status webhooks.
    """

    normalized_recipient = normalize_phone_number(
        recipient_phone_number
    )

    if not normalized_recipient:
        raise MetaWhatsAppAPIError(
            error_message=(
                "Envío de plantilla rechazado: "
                "el número de destino no es válido."
            )
        )

    template_payload = {
        "name": template_name,
        "language": {
            "code": language,
        },
    }

    if components:
        template_payload["components"] = components

    response_data = meta_post(
        resource_path=(
            f"{whatsapp_number.meta_phone_number_id}/messages"
        ),
        whatsapp_business_account=(
            whatsapp_number.whatsapp_business_account
        ),
        payload={
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalized_recipient,
            "type": "template",
            "template": template_payload,
        },
    )

    messages = response_data.get("messages") or []

    if not messages or not messages[0].get("id"):
        raise MetaWhatsAppAPIError(
            error_message=(
                "Envío de plantilla rechazado: "
                "Meta no devolvió un identificador "
                "de mensaje válido."
            )
        )

    message_status = str(messages[0].get("message_status") or "accepted")

    logger.info(
        "[WHATSAPP TEMPLATE] SUBMITTED | number_id=%s | meta_message_id=%s | message_status=%s",
        whatsapp_number.id,
        messages[0]["id"],
        message_status,
    )

    return {
        "meta_message_id": messages[0]["id"],
        "recipient_phone_number": normalized_recipient,
        "message_status": message_status,
        "response_data": response_data,
    }
