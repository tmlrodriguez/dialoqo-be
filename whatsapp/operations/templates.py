import re

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from auditing.operations import schedule_audit_event
from auditing.registry import AUDIT_ACTION_REGISTRY, AUDIT_CATEGORY_REGISTRY

from whatsapp.meta.messages import send_meta_whatsapp_template_message
from whatsapp.meta.templates import (
    create_meta_whatsapp_message_template,
    delete_meta_whatsapp_message_template,
    list_meta_whatsapp_message_templates,
    update_meta_whatsapp_message_template,
)
from whatsapp.models import (
    Conversation,
    Customer,
    Message,
    WhatsAppBusinessAccount,
    WhatsAppMessageTemplate,
    WhatsAppNumber,
)
from whatsapp.operations.assignments import validate_member_whatsapp_number_operation
from whatsapp.realtime.events import (
    schedule_conversation_updated_event,
    schedule_message_created_event,
)
from whatsapp.registry import (
    MESSAGE_DIRECTION_REGISTRY,
    MESSAGE_STATUS_REGISTRY,
    MESSAGE_TEMPLATE_CATEGORY_REGISTRY,
    MESSAGE_TEMPLATE_PARAMETER_FORMAT_REGISTRY,
    MESSAGE_TEMPLATE_STATUS_REGISTRY,
    MESSAGE_TYPE_REGISTRY,
)
from whatsapp.utils.webhooks import normalize_phone_number

# Define your template operations here.

def normalize_template_status(value):
    """
        DOCSTRING: Normalize Template Status

        Description:
        - Normalize a Meta template status into the Dialoqo registry.
    """

    normalized_value = str(value or "").upper()
    valid_values = {choice.value for choice in MESSAGE_TEMPLATE_STATUS_REGISTRY}

    if normalized_value in valid_values:
        return normalized_value

    return MESSAGE_TEMPLATE_STATUS_REGISTRY.UNKNOWN


def normalize_template_category(value):
    """
        DOCSTRING: Normalize Template Category

        Description:
        - Normalize a Meta template category into the Dialoqo registry.
    """

    normalized_value = str(value or "").upper()
    valid_values = {choice.value for choice in MESSAGE_TEMPLATE_CATEGORY_REGISTRY}

    if normalized_value in valid_values:
        return normalized_value

    return MESSAGE_TEMPLATE_CATEGORY_REGISTRY.UNKNOWN


def normalize_template_parameter_format(value):
    """
        DOCSTRING: Normalize Template Parameter Format

        Description:
        - Normalize the variable format reported by Meta.
    """

    normalized_value = str(value or "").upper()
    valid_values = {choice.value for choice in MESSAGE_TEMPLATE_PARAMETER_FORMAT_REGISTRY}

    if normalized_value in valid_values:
        return normalized_value

    return MESSAGE_TEMPLATE_PARAMETER_FORMAT_REGISTRY.POSITIONAL


@transaction.atomic
def synchronize_whatsapp_message_templates(whatsapp_business_account, actor, audit_event=True):
    """
        DOCSTRING: Synchronize WhatsApp Message Templates

        Description:
        - Synchronize the complete Meta WhatsApp message-template catalog into Dialoqo.
        - Create templates that do not yet exist locally.
        - Update templates that already exist locally.
        - Mark previously synchronized templates as unavailable when they disappear from Meta.
        - Record explicit template synchronization in the centralized audit subsystem.

        Notes:
        - Synchronization is idempotent.
        - Meta remains authoritative for template identifiers, definitions, categories, languages, and lifecycle states.
        - Existing templates preserve their original created_by value.
        - Newly synchronized templates record the current actor as created_by and updated_by.
        - Templates removed from Meta remain preserved locally for historical traceability.
        - A synchronization failure rolls back the complete local synchronization transaction.
        - audit_event may be disabled when synchronization is executed internally after another explicit template operation.
        - Template content and parameter values are not copied into audit metadata.
    """

    account = WhatsAppBusinessAccount.objects.select_for_update().select_related("company").get(id=whatsapp_business_account.id)
    meta_templates = list_meta_whatsapp_message_templates(whatsapp_business_account=account)

    synchronized_at = timezone.now()
    seen_meta_template_ids = set()
    created_count = 0
    updated_count = 0

    for meta_template in meta_templates:
        meta_template_id = str(meta_template.get("id") or "")
        name = meta_template.get("name") or ""
        language = meta_template.get("language") or ""

        if not meta_template_id or not name or not language:
            continue

        seen_meta_template_ids.add(meta_template_id)

        template = WhatsAppMessageTemplate.objects.select_for_update().filter(meta_template_id=meta_template_id).first()
        category = normalize_template_category(meta_template.get("category"))
        status = normalize_template_status(meta_template.get("status"))
        parameter_format = normalize_template_parameter_format(meta_template.get("parameter_format"))
        components = meta_template.get("components") or []
        quality_score = meta_template.get("quality_score") or {}
        rejection_reason = meta_template.get("rejected_reason") or meta_template.get("rejection_reason") or ""

        if template is None:
            WhatsAppMessageTemplate.objects.create(
                whatsapp_business_account=account,
                meta_template_id=meta_template_id,
                name=name,
                language=language,
                category=category,
                status=status,
                parameter_format=parameter_format,
                components=components,
                quality_score=quality_score,
                rejection_reason=rejection_reason,
                is_available_in_meta=True,
                last_synced_at=synchronized_at,
                removed_from_meta_at=None,
                is_active=True,
                created_by=actor,
                updated_by=actor,
            )

            created_count += 1
            continue

        template.whatsapp_business_account = account
        template.name = name
        template.language = language
        template.category = category
        template.status = status
        template.parameter_format = parameter_format
        template.components = components
        template.quality_score = quality_score
        template.rejection_reason = rejection_reason
        template.is_available_in_meta = True
        template.last_synced_at = synchronized_at
        template.removed_from_meta_at = None
        template.is_active = True
        template.updated_by = actor
        template.save(update_fields=["whatsapp_business_account", "name", "language", "category", "status", "parameter_format", "components", "quality_score", "rejection_reason", "is_available_in_meta", "last_synced_at", "removed_from_meta_at", "is_active", "updated_by", "updated_at"])

        updated_count += 1

    missing_templates = WhatsAppMessageTemplate.objects.select_for_update().filter(whatsapp_business_account=account, is_available_in_meta=True)

    if seen_meta_template_ids:
        missing_templates = missing_templates.exclude(meta_template_id__in=seen_meta_template_ids)

    removed_count = missing_templates.update(is_available_in_meta=False, removed_from_meta_at=synchronized_at, updated_by=actor, updated_at=synchronized_at)

    if audit_event:
        schedule_audit_event(
            category=AUDIT_CATEGORY_REGISTRY.WHATSAPP,
            action=AUDIT_ACTION_REGISTRY.SYNCHRONIZE,
            description="Plantillas de WhatsApp sincronizadas con Meta.",
            actor=actor,
            company=account.company,
            target=account,
            metadata={
                "whatsapp_business_account_id": account.id,
                "meta_waba_id": account.meta_waba_id,
                "created": created_count,
                "updated": updated_count,
                "removed": removed_count,
                "total": len(meta_templates),
                "synchronized_at": synchronized_at.isoformat(),
            },
        )

    return {
        "created": created_count,
        "updated": updated_count,
        "removed": removed_count,
        "total": len(meta_templates),
    }


def validate_template_for_sending(whatsapp_message_template, whatsapp_number):
    """
        DOCSTRING: Validate Template For Sending

        Description:
        - Validate whether a WhatsApp template may be sent from the supplied corporate WhatsApp number.

        Notes:
        - Template, WABA, number, and company must belong to the same tenant.
        - Only approved, active, Meta-available templates may be sent.
    """

    template_account = whatsapp_message_template.whatsapp_business_account
    number_account = whatsapp_number.whatsapp_business_account
    company = whatsapp_number.company

    if template_account.id != number_account.id:
        raise ValidationError({"template_id": "Envío de plantilla rechazado: la plantilla no pertenece a la cuenta de WhatsApp Business del número."})

    if template_account.company_id != company.id:
        raise ValidationError({"template_id": "Envío de plantilla rechazado: la plantilla pertenece a otra empresa."})

    if not whatsapp_message_template.is_active:
        raise ValidationError({"template_id": "Envío de plantilla rechazado: la plantilla se encuentra inactiva."})

    if not whatsapp_message_template.is_available_in_meta:
        raise ValidationError({"template_id": "Envío de plantilla rechazado: la plantilla ya no se encuentra disponible en Meta."})

    if whatsapp_message_template.status != MESSAGE_TEMPLATE_STATUS_REGISTRY.APPROVED:
        raise ValidationError({"template_id": "Envío de plantilla rechazado: la plantilla no se encuentra aprobada."})

    return whatsapp_message_template


def extract_template_placeholders(text):
    """
        DOCSTRING: Extract Template Placeholders

        Description:
        - Extract WhatsApp template variable placeholders from text.

        Notes:
        - Positional and named placeholders are supported.
    """

    if not text:
        return []

    return re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", str(text))


def validate_template_send_components(whatsapp_message_template, send_components):
    """
        DOCSTRING: Validate Template Send Components

        Description:
        - Validate outbound template parameters against the synchronized template definition.

        Notes:
        - BODY and HEADER placeholder requirements are validated.
        - Button component structure is preserved for Meta validation.
        - Meta remains the final authority for template parameter semantics.
    """

    send_components = send_components or []
    send_component_map = {}

    for component in send_components:
        component_type = str(component.get("type") or "").upper()

        if component_type in ["HEADER", "BODY"]:
            send_component_map[component_type] = component

    for template_component in whatsapp_message_template.components or []:
        component_type = str(template_component.get("type") or "").upper()

        if component_type not in ["HEADER", "BODY"]:
            continue

        placeholders = extract_template_placeholders(template_component.get("text") or "")

        if not placeholders:
            continue

        supplied_component = send_component_map.get(component_type)

        if supplied_component is None:
            raise ValidationError({"components": f"Envío de plantilla rechazado: faltan parámetros para {component_type}."})

        supplied_parameters = supplied_component.get("parameters") or []

        if whatsapp_message_template.parameter_format == MESSAGE_TEMPLATE_PARAMETER_FORMAT_REGISTRY.POSITIONAL:
            if len(supplied_parameters) != len(placeholders):
                raise ValidationError({"components": f"Envío de plantilla rechazado: {component_type} requiere {len(placeholders)} parámetro(s)."})

        elif whatsapp_message_template.parameter_format == MESSAGE_TEMPLATE_PARAMETER_FORMAT_REGISTRY.NAMED:
            supplied_names = {str(parameter.get("parameter_name") or "") for parameter in supplied_parameters}
            missing_names = [placeholder for placeholder in placeholders if placeholder not in supplied_names]

            if missing_names:
                raise ValidationError({"components": f"Envío de plantilla rechazado: faltan parámetros nombrados: {', '.join(missing_names)}."})

    return send_components


def render_template_body_text(whatsapp_message_template, send_components):
    """
        DOCSTRING: Render Template Body Text

        Description:
        - Build a readable Dialoqo preview of an outbound template body.

        Notes:
        - Meta remains responsible for final recipient rendering.
        - This representation is used only for Dialoqo conversation history.
    """

    body_component = None

    for component in whatsapp_message_template.components or []:
        if str(component.get("type") or "").upper() == "BODY":
            body_component = component
            break

    if body_component is None:
        return whatsapp_message_template.name

    rendered_text = body_component.get("text") or whatsapp_message_template.name
    body_send_component = None

    for component in send_components or []:
        if str(component.get("type") or "").upper() == "BODY":
            body_send_component = component
            break

    if body_send_component is None:
        return rendered_text

    parameters = body_send_component.get("parameters") or []

    if whatsapp_message_template.parameter_format == MESSAGE_TEMPLATE_PARAMETER_FORMAT_REGISTRY.POSITIONAL:
        for index, parameter in enumerate(parameters, start=1):
            value = parameter.get("text") or parameter.get("value") or ""
            rendered_text = rendered_text.replace("{{" + str(index) + "}}", str(value))

    else:
        for parameter in parameters:
            parameter_name = parameter.get("parameter_name") or ""
            value = parameter.get("text") or parameter.get("value") or ""

            if parameter_name:
                rendered_text = rendered_text.replace("{{" + parameter_name + "}}", str(value))

    return rendered_text


def validate_whatsapp_number_for_template_send(whatsapp_number):
    """
        DOCSTRING: Validate WhatsApp Number For Template Send

        Description:
        - Validate the complete tenant chain required for outbound template messaging.

        Notes:
        - Company, branch, number, and WABA must form one consistent tenant boundary.
        - Cross-company relationships are rejected even if inconsistent database data exists.
    """

    company = whatsapp_number.company
    branch = whatsapp_number.branch
    account = whatsapp_number.whatsapp_business_account

    if branch.company_id != company.id:
        raise ValidationError({"branch": "Envío de plantilla rechazado: la sucursal no pertenece a la empresa."})

    if account.company_id != company.id:
        raise ValidationError({"whatsapp_business_account": "Envío de plantilla rechazado: la cuenta de WhatsApp Business no pertenece a la empresa."})

    if not company.is_active:
        raise ValidationError({"company": "Envío de plantilla rechazado: la empresa se encuentra inactiva."})

    if not branch.is_active:
        raise ValidationError({"branch": "Envío de plantilla rechazado: la sucursal se encuentra inactiva."})

    if not whatsapp_number.is_active:
        raise ValidationError({"whatsapp_number": "Envío de plantilla rechazado: el número se encuentra inactivo."})

    if not whatsapp_number.is_connected:
        raise ValidationError({"whatsapp_number": "Envío de plantilla rechazado: el número no se encuentra conectado."})

    if not account.is_active:
        raise ValidationError({"whatsapp_business_account": "Envío de plantilla rechazado: la cuenta de WhatsApp Business se encuentra inactiva."})

    if not account.is_connected:
        raise ValidationError({"whatsapp_business_account": "Envío de plantilla rechazado: la cuenta de WhatsApp Business no se encuentra conectada."})

    if not account.is_webhook_configured:
        raise ValidationError({"whatsapp_business_account": "Envío de plantilla rechazado: el webhook no se encuentra configurado."})

    return whatsapp_number


@transaction.atomic
def persist_outbound_template_message(conversation, whatsapp_message_template, meta_message_id, recipient_phone_number, send_components, actor, meta_message_status=""):
    """
        DOCSTRING: Persist Outbound Template Message

        Description:
        - Persist a Meta-accepted outbound template message inside Dialoqo.
        - Update the corresponding conversation preview.
        - Record the user-initiated template send in the centralized audit subsystem.

        Notes:
        - The initial local state is PENDING.
        - status_updated_at remains null until Meta reports a delivery status, avoiding clock and timestamp-precision conflicts.
        - Existing webhook status logic later advances the lifecycle.
        - content_data preserves template identity and parameters used at send time.
        - Meta submission status is stored separately from confirmed delivery state.
        - Audit metadata identifies the template and resulting message without storing template parameter values.
        - Duplicate persistence does not create a duplicate SEND audit event.
    """

    locked_conversation = Conversation.objects.select_for_update(of=("self",)).select_related(
        "customer",
        "whatsapp_number",
        "whatsapp_number__company",
        "whatsapp_number__branch",
    ).get(id=conversation.id)

    message_timestamp = timezone.now()

    existing_message = Message.objects.filter(meta_message_id=meta_message_id).first()

    if existing_message:
        return existing_message, False

    try:
        with transaction.atomic():
            message = Message.objects.create(
                conversation=locked_conversation,
                meta_message_id=meta_message_id,
                direction=MESSAGE_DIRECTION_REGISTRY.OUTBOUND,
                message_type=MESSAGE_TYPE_REGISTRY.TEMPLATE,
                status=MESSAGE_STATUS_REGISTRY.PENDING,
                sender_phone_number=locked_conversation.whatsapp_number.phone_number,
                recipient_phone_number=recipient_phone_number,
                text_body=render_template_body_text(whatsapp_message_template, send_components),
                original_text_body="",
                content_data={
                    "template": {
                        "template_id": whatsapp_message_template.id,
                        "meta_template_id": whatsapp_message_template.meta_template_id,
                        "name": whatsapp_message_template.name,
                        "language": whatsapp_message_template.language,
                        "category": whatsapp_message_template.category,
                        "components": send_components or [],
                        "meta_message_status": meta_message_status,
                    }
                },
                message_timestamp=message_timestamp,
                meta_event_timestamp=message_timestamp,
                status_updated_at=None,
                created_by=actor,
                updated_by=actor,
            )

    except IntegrityError:
        return Message.objects.get(meta_message_id=meta_message_id), False

    locked_conversation.last_message = message
    locked_conversation.last_message_at = message.message_timestamp
    locked_conversation.updated_by = actor
    locked_conversation.save(update_fields=["last_message", "last_message_at", "updated_by", "updated_at"])

    schedule_message_created_event(message=message)
    schedule_conversation_updated_event(conversation=locked_conversation)

    schedule_audit_event(
        category=AUDIT_CATEGORY_REGISTRY.WHATSAPP,
        action=AUDIT_ACTION_REGISTRY.SEND,
        description="Plantilla de WhatsApp aceptada por Meta desde Dialoqo.",
        actor=actor,
        company=locked_conversation.whatsapp_number.company,
        branch=locked_conversation.whatsapp_number.branch,
        target=message,
        metadata={
            "message_id": message.id,
            "meta_message_id": message.meta_message_id,
            "conversation_id": locked_conversation.id,
            "customer_id": locked_conversation.customer_id,
            "whatsapp_number_id": locked_conversation.whatsapp_number_id,
            "whatsapp_message_template_id": whatsapp_message_template.id,
            "meta_template_id": whatsapp_message_template.meta_template_id,
            "template_name": whatsapp_message_template.name,
            "template_language": whatsapp_message_template.language,
            "template_category": whatsapp_message_template.category,
            "message_type": message.message_type,
            "direction": message.direction,
        },
    )

    return message, True


def send_template_to_existing_conversation(conversation, whatsapp_message_template, send_components, actor):
    """
        DOCSTRING: Send Template To Existing Conversation

        Description:
        - Send an approved template into an existing monitored conversation.

        Notes:
        - Meta transmission occurs before persistence.
        - No fake local message is created if Meta rejects the request.
        - Successful persistence records the corresponding SEND audit event.
    """

    conversation = Conversation.objects.select_related(
        "customer",
        "whatsapp_number",
        "whatsapp_number__company",
        "whatsapp_number__branch",
        "whatsapp_number__whatsapp_business_account",
    ).get(id=conversation.id)

    whatsapp_number = conversation.whatsapp_number

    validate_member_whatsapp_number_operation(whatsapp_number=whatsapp_number, actor=actor)
    validate_whatsapp_number_for_template_send(whatsapp_number)
    validate_template_for_sending(whatsapp_message_template, whatsapp_number)
    validate_template_send_components(whatsapp_message_template, send_components)

    meta_result = send_meta_whatsapp_template_message(
        whatsapp_number=whatsapp_number,
        recipient_phone_number=conversation.customer.phone_number,
        template_name=whatsapp_message_template.name,
        language=whatsapp_message_template.language,
        components=send_components,
    )

    message, created = persist_outbound_template_message(
        conversation=conversation,
        whatsapp_message_template=whatsapp_message_template,
        meta_message_id=meta_result["meta_message_id"],
        recipient_phone_number=meta_result["recipient_phone_number"],
        send_components=send_components,
        actor=actor,
        meta_message_status=meta_result.get("message_status", "accepted"),
    )

    return {
        "message": message,
        "conversation": conversation,
        "created": created,
        "meta": {
            "message_id": meta_result["meta_message_id"],
            "message_status": meta_result.get("message_status", "accepted"),
        },
    }


def send_template_to_new_conversation(whatsapp_number, recipient_phone_number, whatsapp_message_template, send_components, actor):
    """
        DOCSTRING: Send Template To New Conversation

        Description:
        - Initiate a business conversation using an approved WhatsApp template.
        - Send the template to Meta before creating new Dialoqo customer or conversation records.

        Notes:
        - Failed Meta sends never create empty conversations.
        - Existing customer and conversation records are reused when already present.
        - The operation creates local resources only after Meta returns a valid message identifier.
        - Successful persistence records the corresponding SEND audit event.
    """

    whatsapp_number = WhatsAppNumber.objects.select_related("company", "branch", "whatsapp_business_account").get(id=whatsapp_number.id)

    validate_member_whatsapp_number_operation(whatsapp_number=whatsapp_number, actor=actor)
    validate_whatsapp_number_for_template_send(whatsapp_number)
    validate_template_for_sending(whatsapp_message_template, whatsapp_number)
    validate_template_send_components(whatsapp_message_template, send_components)

    normalized_recipient = normalize_phone_number(recipient_phone_number)

    if not normalized_recipient:
        raise ValidationError({"recipient_phone_number": "Envío de plantilla rechazado: el número de destino no es válido."})

    meta_result = send_meta_whatsapp_template_message(
        whatsapp_number=whatsapp_number,
        recipient_phone_number=normalized_recipient,
        template_name=whatsapp_message_template.name,
        language=whatsapp_message_template.language,
        components=send_components,
    )

    with transaction.atomic():
        customer, _ = Customer.objects.get_or_create(company=whatsapp_number.company, phone_number=normalized_recipient)
        conversation, _ = Conversation.objects.get_or_create(whatsapp_number=whatsapp_number, customer=customer)

        message, created = persist_outbound_template_message(
            conversation=conversation,
            whatsapp_message_template=whatsapp_message_template,
            meta_message_id=meta_result["meta_message_id"],
            recipient_phone_number=normalized_recipient,
            send_components=send_components,
            actor=actor,
            meta_message_status=meta_result.get("message_status", "accepted"),
        )

    return {
        "customer": customer,
        "conversation": conversation,
        "message": message,
        "created": created,
        "meta": {
            "message_id": meta_result["meta_message_id"],
            "message_status": meta_result.get("message_status", "accepted"),
        },
    }


def create_whatsapp_message_template(whatsapp_business_account, validated_data, actor):
    """
        DOCSTRING: Create WhatsApp Message Template

        Description:
        - Submit a new template to Meta.
        - Synchronize the WABA template catalog after Meta accepts the request.
        - Record the template creation in the centralized audit subsystem.

        Notes:
        - Meta remains authoritative for template ID and lifecycle state.
        - Internal synchronization does not generate an additional SYNCHRONIZE audit event.
        - Template body content and example parameter values are not copied into audit metadata.
    """

    create_meta_whatsapp_message_template(
        whatsapp_business_account=whatsapp_business_account,
        name=validated_data["name"],
        language=validated_data["language"],
        category=validated_data["category"],
        parameter_format=validated_data["parameter_format"],
        components=validated_data["components"],
    )

    synchronize_whatsapp_message_templates(
        whatsapp_business_account=whatsapp_business_account,
        actor=actor,
        audit_event=False,
    )

    template = WhatsAppMessageTemplate.objects.select_related("whatsapp_business_account__company").filter(
        whatsapp_business_account=whatsapp_business_account,
        name=validated_data["name"],
        language=validated_data["language"],
    ).first()

    if template:
        schedule_audit_event(
            category=AUDIT_CATEGORY_REGISTRY.WHATSAPP,
            action=AUDIT_ACTION_REGISTRY.CREATE,
            description="Plantilla de WhatsApp creada en Meta.",
            actor=actor,
            company=template.whatsapp_business_account.company,
            target=template,
            metadata={
                "whatsapp_message_template_id": template.id,
                "whatsapp_business_account_id": template.whatsapp_business_account_id,
                "meta_template_id": template.meta_template_id,
                "name": template.name,
                "language": template.language,
                "category": template.category,
                "status": template.status,
                "parameter_format": template.parameter_format,
            },
        )

    return template


def update_whatsapp_message_template(whatsapp_message_template, validated_data, actor):
    """
        DOCSTRING: Update WhatsApp Message Template

        Description:
        - Submit supported template modifications to Meta.
        - Resynchronize the corresponding WABA template catalog.
        - Record the successful update in the centralized audit subsystem.

        Notes:
        - Meta remains authoritative for the resulting template state.
        - Internal synchronization does not generate an additional SYNCHRONIZE audit event.
        - Audit metadata records changed field names without storing complete template components.
    """

    changed_fields = list(validated_data.keys())

    previous_category = whatsapp_message_template.category
    previous_status = whatsapp_message_template.status

    update_meta_whatsapp_message_template(
        whatsapp_message_template=whatsapp_message_template,
        category=validated_data.get("category"),
        components=validated_data.get("components"),
    )

    synchronize_whatsapp_message_templates(
        whatsapp_business_account=whatsapp_message_template.whatsapp_business_account,
        actor=actor,
        audit_event=False,
    )

    template = WhatsAppMessageTemplate.objects.select_related("whatsapp_business_account__company").get(id=whatsapp_message_template.id)

    schedule_audit_event(
        category=AUDIT_CATEGORY_REGISTRY.WHATSAPP,
        action=AUDIT_ACTION_REGISTRY.UPDATE,
        description="Plantilla de WhatsApp actualizada en Meta.",
        actor=actor,
        company=template.whatsapp_business_account.company,
        target=template,
        metadata={
            "whatsapp_message_template_id": template.id,
            "whatsapp_business_account_id": template.whatsapp_business_account_id,
            "meta_template_id": template.meta_template_id,
            "name": template.name,
            "language": template.language,
            "changed_fields": changed_fields,
            "previous_category": previous_category,
            "current_category": template.category,
            "previous_status": previous_status,
            "current_status": template.status,
        },
    )

    return template


def delete_whatsapp_message_template(whatsapp_message_template, actor):
    """
        DOCSTRING: Delete WhatsApp Message Template

        Description:
        - Request template deletion from Meta.
        - Preserve the local historical template record.
        - Mark the local template unavailable and inactive.
        - Record the deletion in the centralized audit subsystem.

        Notes:
        - Local history remains preserved after Meta deletion.
        - Audit metadata preserves template identity without storing template content.
        - The audit event is emitted only after successful Meta deletion and local lifecycle update.
    """

    template = WhatsAppMessageTemplate.objects.select_related("whatsapp_business_account__company").get(id=whatsapp_message_template.id)

    delete_meta_whatsapp_message_template(template)

    template.is_available_in_meta = False
    template.is_active = False
    template.removed_from_meta_at = timezone.now()
    template.updated_by = actor
    template.save(update_fields=["is_available_in_meta", "is_active", "removed_from_meta_at", "updated_by", "updated_at"])

    schedule_audit_event(
        category=AUDIT_CATEGORY_REGISTRY.WHATSAPP,
        action=AUDIT_ACTION_REGISTRY.DELETE,
        description="Plantilla de WhatsApp eliminada de Meta.",
        actor=actor,
        company=template.whatsapp_business_account.company,
        target=template,
        metadata={
            "whatsapp_message_template_id": template.id,
            "whatsapp_business_account_id": template.whatsapp_business_account_id,
            "meta_template_id": template.meta_template_id,
            "name": template.name,
            "language": template.language,
            "category": template.category,
            "status": template.status,
            "removed_from_meta_at": template.removed_from_meta_at.isoformat(),
        },
    )

    return template
