from datetime import datetime, timezone as datetime_timezone

from whatsapp.registry import MESSAGE_DIRECTION_REGISTRY, MESSAGE_TYPE_REGISTRY

# Define your webhook helpers here.

def parse_meta_timestamp(timestamp_value):
    """
        DOCSTRING: Parse Meta Timestamp

        Description:
        - Convert a Meta Unix timestamp value into a timezone-aware UTC datetime.

        Notes:
        - Invalid or missing timestamp values return None.
        - Webhook ingestion must not fail globally because one event contains a malformed timestamp.
    """

    if timestamp_value is None:
        return None

    try:
        return datetime.fromtimestamp(int(timestamp_value), tz=datetime_timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def normalize_phone_number(phone_number):
    """
        DOCSTRING: Normalize Phone Number

        Description:
        - Normalize a phone number into a digits-only value used exclusively for comparison.

        Notes:
        - Stored phone-number formatting is not modified by this helper.
        - The helper allows comparison between E.164 values containing a leading plus sign and Meta values containing digits only.
    """

    if not phone_number:
        return ""

    return "".join(character for character in str(phone_number) if character.isdigit())


def phone_numbers_match(first_phone_number, second_phone_number):
    """
        DOCSTRING: Phone Numbers Match

        Description:
        - Determine whether two WhatsApp phone-number representations identify the same number.

        Notes:
        - Comparison is performed using normalized digits-only values.
        - Missing values never produce a positive match.
    """

    first_normalized = normalize_phone_number(first_phone_number)
    second_normalized = normalize_phone_number(second_phone_number)

    if not first_normalized or not second_normalized:
        return False

    return first_normalized == second_normalized


def resolve_message_type(meta_message_type):
    """
        DOCSTRING: Resolve Message Type

        Description:
        - Normalize a Meta WhatsApp message type into the Dialoqo message type registry.

        Notes:
        - Unknown Meta message types are persisted as UNSUPPORTED.
        - Unsupported events remain visible in monitored history instead of silently disappearing.
    """

    mapping = {
        "text": MESSAGE_TYPE_REGISTRY.TEXT,
        "image": MESSAGE_TYPE_REGISTRY.IMAGE,
        "audio": MESSAGE_TYPE_REGISTRY.AUDIO,
        "video": MESSAGE_TYPE_REGISTRY.VIDEO,
        "document": MESSAGE_TYPE_REGISTRY.DOCUMENT,
        "sticker": MESSAGE_TYPE_REGISTRY.STICKER,
        "location": MESSAGE_TYPE_REGISTRY.LOCATION,
        "contacts": MESSAGE_TYPE_REGISTRY.CONTACTS,
        "interactive": MESSAGE_TYPE_REGISTRY.INTERACTIVE,
        "template": MESSAGE_TYPE_REGISTRY.TEMPLATE,
        "reaction": MESSAGE_TYPE_REGISTRY.REACTION,
        "button": MESSAGE_TYPE_REGISTRY.BUTTON,
        "order": MESSAGE_TYPE_REGISTRY.ORDER,
    }

    return mapping.get(meta_message_type, MESSAGE_TYPE_REGISTRY.UNSUPPORTED)


def extract_message_text(message_data, message_type):
    """
        DOCSTRING: Extract Message Text

        Description:
        - Extract the primary human-readable text representation from a WhatsApp message event.

        Notes:
        - Media messages use their caption when available.
        - Interactive and button responses expose their visible selection text.
        - Reaction messages expose their emoji.
        - Message types without textual content return an empty string.
    """

    if message_type == MESSAGE_TYPE_REGISTRY.TEXT:
        return (message_data.get("text") or {}).get("body") or ""

    if message_type == MESSAGE_TYPE_REGISTRY.IMAGE:
        return (message_data.get("image") or {}).get("caption") or ""

    if message_type == MESSAGE_TYPE_REGISTRY.VIDEO:
        return (message_data.get("video") or {}).get("caption") or ""

    if message_type == MESSAGE_TYPE_REGISTRY.DOCUMENT:
        return (message_data.get("document") or {}).get("caption") or ""

    if message_type == MESSAGE_TYPE_REGISTRY.BUTTON:
        return (message_data.get("button") or {}).get("text") or ""

    if message_type == MESSAGE_TYPE_REGISTRY.REACTION:
        return (message_data.get("reaction") or {}).get("emoji") or ""

    if message_type == MESSAGE_TYPE_REGISTRY.INTERACTIVE:
        interactive_data = message_data.get("interactive") or {}
        interactive_type = interactive_data.get("type")

        if interactive_type == "button_reply":
            return (interactive_data.get("button_reply") or {}).get("title") or ""

        if interactive_type == "list_reply":
            return (interactive_data.get("list_reply") or {}).get("title") or ""

    return ""


def extract_message_content(message_data, message_type, meta_message_type):
    """
        DOCSTRING: Extract Message Content

        Description:
        - Build a normalized structured-content representation for a WhatsApp message.
        - Preserve information required by the Dialoqo frontend without storing the complete raw webhook payload.

        Notes:
        - Structured content is stored in Message.content_data.
        - Sensitive integration credentials are never included.
        - Unsupported messages preserve only their original Meta type.
    """

    if message_type == MESSAGE_TYPE_REGISTRY.LOCATION:
        location_data = message_data.get("location") or {}

        return {
            "latitude": location_data.get("latitude"),
            "longitude": location_data.get("longitude"),
            "name": location_data.get("name") or "",
            "address": location_data.get("address") or "",
        }

    if message_type == MESSAGE_TYPE_REGISTRY.CONTACTS:
        return {"contacts": message_data.get("contacts") or []}

    if message_type == MESSAGE_TYPE_REGISTRY.INTERACTIVE:
        interactive_data = message_data.get("interactive") or {}

        return {
            "type": interactive_data.get("type") or "",
            "button_reply": interactive_data.get("button_reply") or {},
            "list_reply": interactive_data.get("list_reply") or {},
        }

    if message_type == MESSAGE_TYPE_REGISTRY.BUTTON:
        button_data = message_data.get("button") or {}

        return {
            "text": button_data.get("text") or "",
            "payload": button_data.get("payload") or "",
        }

    if message_type == MESSAGE_TYPE_REGISTRY.REACTION:
        reaction_data = message_data.get("reaction") or {}

        return {
            "emoji": reaction_data.get("emoji") or "",
            "message_id": reaction_data.get("message_id") or "",
        }

    if message_type == MESSAGE_TYPE_REGISTRY.ORDER:
        return {"order": message_data.get("order") or {}}

    if message_type == MESSAGE_TYPE_REGISTRY.TEMPLATE:
        return {"template": message_data.get("template") or {}}

    if message_type in [MESSAGE_TYPE_REGISTRY.IMAGE, MESSAGE_TYPE_REGISTRY.AUDIO, MESSAGE_TYPE_REGISTRY.VIDEO, MESSAGE_TYPE_REGISTRY.DOCUMENT, MESSAGE_TYPE_REGISTRY.STICKER]:
        media_data = message_data.get(message_type.lower()) or {}

        return {
            "caption": media_data.get("caption") or "",
            "filename": media_data.get("filename") or "",
            "mime_type": media_data.get("mime_type") or "",
            "sha256": media_data.get("sha256") or "",
        }

    if message_type == MESSAGE_TYPE_REGISTRY.UNSUPPORTED:
        return {"meta_type": meta_message_type or ""}

    return {}


def extract_media_payload(message_data, message_type):
    """
        DOCSTRING: Extract Media Payload

        Description:
        - Extract media metadata from a supported WhatsApp media message.

        Notes:
        - Binary media content is not downloaded during webhook ingestion.
        - Media binary retrieval and private storage are handled by the media-storage subsystem.
        - Non-media messages return None.
    """

    if message_type not in [MESSAGE_TYPE_REGISTRY.IMAGE, MESSAGE_TYPE_REGISTRY.AUDIO, MESSAGE_TYPE_REGISTRY.VIDEO, MESSAGE_TYPE_REGISTRY.DOCUMENT, MESSAGE_TYPE_REGISTRY.STICKER]:
        return None

    media_data = message_data.get(message_type.lower()) or {}
    meta_media_id = media_data.get("id")

    if not meta_media_id:
        return None

    return {
        "meta_media_id": meta_media_id,
        "mime_type": media_data.get("mime_type") or "application/octet-stream",
        "original_filename": media_data.get("filename") or "",
        "checksum": media_data.get("sha256") or "",
    }


def extract_context_message_id(message_data):
    """
        DOCSTRING: Extract Context Message ID

        Description:
        - Resolve the Meta message identifier referenced by a reply, reaction, edit, or related message event.

        Notes:
        - Multiple supported payload locations are inspected because event structure may differ by message type and Coexistence event.
        - Missing context returns an empty string.
    """

    context_data = message_data.get("context") or {}
    reaction_data = message_data.get("reaction") or {}

    if reaction_data.get("message_id"):
        return reaction_data["message_id"]

    if context_data.get("id"):
        return context_data["id"]

    return ""


def extract_edit_event(message_data):
    """
        DOCSTRING: Extract Edit Event

        Description:
        - Detect and normalize a WhatsApp message-edit event when one is present.

        Notes:
        - The parser accepts multiple compatible edit-event shapes to remain tolerant of Coexistence webhook variations.
        - A valid edit requires an original message identifier and updated message content.
        - Non-edit messages return None.
    """

    message_type = message_data.get("type") or ""
    edited_data = message_data.get("edited") or message_data.get("edit") or {}

    if message_type not in ["edited", "edit"] and not edited_data:
        return None

    context_data = message_data.get("context") or {}
    original_message_id = message_data.get("original_message_id") or edited_data.get("original_message_id") or edited_data.get("message_id") or context_data.get("id") or ""
    edited_message_data = edited_data.get("message") if isinstance(edited_data, dict) else None

    if not isinstance(edited_message_data, dict):
        edited_message_data = message_data

    return {
        "original_message_id": original_message_id,
        "message_data": edited_message_data,
    }


def extract_revocation_event(message_data):
    """
        DOCSTRING: Extract Revocation Event

        Description:
        - Detect and normalize a WhatsApp message-revocation or deletion event.

        Notes:
        - The parser accepts common revocation and deletion event representations.
        - Revoked records remain stored in Dialoqo and are rendered as deleted messages.
        - Non-revocation messages return None.
    """

    message_type = message_data.get("type") or ""
    revoked_data = message_data.get("revoked") or message_data.get("revoke") or message_data.get("deleted") or {}

    if message_type not in ["revoked", "revoke", "deleted"] and not revoked_data:
        return None

    context_data = message_data.get("context") or {}
    original_message_id = message_data.get("original_message_id") or context_data.get("id") or ""

    if isinstance(revoked_data, dict):
        original_message_id = revoked_data.get("message_id") or revoked_data.get("id") or original_message_id

    return {"original_message_id": original_message_id}


def resolve_message_direction(message_data, whatsapp_number, metadata):
    """
        DOCSTRING: Resolve Message Direction

        Description:
        - Determine whether a synchronized WhatsApp message is inbound or outbound relative to the corporate WhatsApp number.

        Notes:
        - Messages whose sender matches the corporate WhatsApp number are treated as OUTBOUND.
        - All other message senders are treated as INBOUND.
        - This supports Coexistence synchronization when messages originate from the WhatsApp Business App.
    """

    sender_phone_number = message_data.get("from")
    display_phone_number = metadata.get("display_phone_number")

    if phone_numbers_match(sender_phone_number, whatsapp_number.phone_number):
        return MESSAGE_DIRECTION_REGISTRY.OUTBOUND

    if display_phone_number and phone_numbers_match(sender_phone_number, display_phone_number):
        return MESSAGE_DIRECTION_REGISTRY.OUTBOUND

    return MESSAGE_DIRECTION_REGISTRY.INBOUND


def resolve_customer_phone_number(message_data, direction, whatsapp_number, metadata, contacts):
    """
        DOCSTRING: Resolve Customer Phone Number

        Description:
        - Resolve the external participant phone number associated with a WhatsApp message.

        Notes:
        - INBOUND messages use their sender.
        - OUTBOUND synchronized messages inspect recipient fields and webhook contacts.
        - The corporate WhatsApp number is excluded from customer resolution.
        - Missing customer identity causes the event to be ignored safely.
    """

    if direction == MESSAGE_DIRECTION_REGISTRY.INBOUND:
        return message_data.get("from") or ""

    candidate_phone_number = message_data.get("to") or message_data.get("recipient") or ""

    if candidate_phone_number and not phone_numbers_match(candidate_phone_number, whatsapp_number.phone_number):
        return candidate_phone_number

    display_phone_number = metadata.get("display_phone_number")

    for contact in contacts:
        wa_id = contact.get("wa_id")

        if not wa_id:
            continue

        if phone_numbers_match(wa_id, whatsapp_number.phone_number):
            continue

        if display_phone_number and phone_numbers_match(wa_id, display_phone_number):
            continue

        return wa_id

    return ""


def extract_failure_details(status_data):
    """
        DOCSTRING: Extract Failure Details

        Description:
        - Extract the primary Meta error code and readable failure description from a failed message status.

        Notes:
        - Meta may return multiple errors.
        - Dialoqo stores the first error as the primary delivery failure reason.
        - Specific error_data.details are retained alongside generic error messages.
        - Missing error information produces empty values.
    """

    errors = status_data.get("errors") or []

    if not errors:
        return "", ""

    error_data = errors[0] or {}
    failure_code = str(error_data.get("code") or "")
    failure_message = error_data.get("message") or error_data.get("title") or ""

    error_details = error_data.get("error_data") or {}
    details = error_details.get("details") or ""

    if details and details not in failure_message:
        failure_message = f"{failure_message}: {details}" if failure_message else details

    return failure_code, failure_message
