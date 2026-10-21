from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from access.registry import ROLE_REGISTRY
from auditing.operations import schedule_audit_event
from auditing.registry import AUDIT_ACTION_REGISTRY, AUDIT_CATEGORY_REGISTRY
from organizations.models import UserCompanyAccess

from .meta import MetaWhatsAppAPIError, send_meta_whatsapp_text_message
from .models import (
    Conversation,
    ConversationReadState,
    Customer,
    MediaAttachment,
    Message,
    NumberAssignment,
    WhatsAppNumber,
)
from .realtime import (
    schedule_conversation_read_state_changed_event,
    schedule_conversation_updated_event,
    schedule_message_created_event,
    schedule_message_status_changed_event,
    schedule_message_updated_event,
    schedule_number_assignment_changed_event,
)
from .registry import (
    MESSAGE_DIRECTION_REGISTRY,
    MESSAGE_STATUS_REGISTRY,
    MESSAGE_TYPE_REGISTRY,
)
from .task_operations import schedule_media_attachment_storage
from .webhook import (
    extract_context_message_id,
    extract_edit_event,
    extract_failure_details,
    extract_media_payload,
    extract_message_content,
    extract_message_text,
    extract_revocation_event,
    parse_meta_timestamp,
    resolve_customer_phone_number,
    resolve_message_direction,
    resolve_message_type,
)


# Define your operations here.


def get_current_whatsapp_number_assignment(whatsapp_number):
    """
        DOCSTRING: Get Current WhatsApp Number Assignment

        Description:
        - Resolve the current active member assignment for a WhatsApp number.

        Notes:
        - Only assignments whose lifecycle remains active are considered current.
        - The member relationship is loaded together with the assignment for efficient serialization.
        - None is returned when the WhatsApp number does not currently have an assigned member.
        - Historical assignments remain preserved independently from the current assignment.
    """

    return NumberAssignment.objects.select_related("member").filter(
        whatsapp_number=whatsapp_number,
        is_active=True,
    ).first()


def validate_member_whatsapp_number_operation(whatsapp_number, actor):
    """
        DOCSTRING: Validate Member WhatsApp Number Operation

        Description:
        - Validate that the authenticated actor is an active MEMBER currently assigned to the supplied WhatsApp number.
        - Establish the operational authorization boundary used by outbound messaging actions.

        Notes:
        - MONITOR and administrative roles are never authorized to send through this operation.
        - The assignment must remain active and must not have been explicitly closed.
        - This validation is intentionally repeated inside the operation layer as defense in depth.
    """

    if actor is None or not actor.is_active or actor.role != ROLE_REGISTRY.MEMBER:
        raise ValidationError({
            "member": "Operación rechazada: únicamente un usuario MEMBER activo puede operar el número de WhatsApp."
        })

    assignment_exists = NumberAssignment.objects.filter(
        whatsapp_number=whatsapp_number,
        member=actor,
        is_active=True,
        unassigned_at__isnull=True,
    ).exists()

    if not assignment_exists:
        raise ValidationError({
            "member": "Operación rechazada: el número de WhatsApp no se encuentra asignado al usuario autenticado."
        })

    return True


def validate_whatsapp_number_assignment_member(whatsapp_number, member):
    """
        DOCSTRING: Validate WhatsApp Number Assignment Member

        Description:
        - Validate whether a Dialoqo MEMBER user may receive assignment of a specific WhatsApp number.

        Notes:
        - The user must have the MEMBER role.
        - The user must remain active.
        - The MEMBER must have been created by the administrator that owns the company.
        - Validation is repeated at the operation layer so assignment rules cannot be bypassed.
    """

    if member.role != ROLE_REGISTRY.MEMBER:
        raise ValidationError({
            "member": "Asignación de número rechazada: el usuario seleccionado debe tener el rol MEMBER."
        })

    if not member.is_active:
        raise ValidationError({
            "member": "Asignación de número rechazada: el miembro seleccionado se encuentra inactivo."
        })

    if member.created_by_id != whatsapp_number.company.created_by_id:
        raise ValidationError({
            "member": "Asignación de número rechazada: el miembro seleccionado no pertenece al administrador propietario de la empresa."
        })

    return member


@transaction.atomic
def assign_whatsapp_number(whatsapp_number, member, actor):
    """
        DOCSTRING: Assign WhatsApp Number

        Description:
        - Assign a Dialoqo MEMBER user to a WhatsApp number.
        - Close the previous active assignment before creating the new assignment.
        - Preserve the complete historical responsibility chain.
        - Publish the new assignment state after successful transaction commit.
        - Record the assignment operation in the centralized audit subsystem.

        Notes:
        - Assignment history must always remain preserved.
        - The WhatsApp number is locked to serialize concurrent assignment operations.
        - The assigned user must be an eligible active MEMBER owned by the company administrator.
        - Reassigning the number to the currently assigned member is rejected.
        - Only one active assignment may remain after the transaction completes.
        - Realtime and audit events are never emitted for rolled-back transactions.
        - Reassignment audit metadata preserves both previous and new responsibility identifiers.
    """

    locked_number = WhatsAppNumber.objects.select_for_update().select_related(
        "company",
        "branch",
    ).get(id=whatsapp_number.id)

    validate_whatsapp_number_assignment_member(
        whatsapp_number=locked_number,
        member=member,
    )

    current_assignment = NumberAssignment.objects.select_for_update().filter(
        whatsapp_number=locked_number,
        is_active=True,
    ).first()

    if current_assignment and current_assignment.member_id == member.id:
        raise ValidationError({
            "member": "Asignación de número rechazada: el miembro ya se encuentra asignado a este número."
        })

    previous_assignment_id = current_assignment.id if current_assignment else None
    previous_member_id = current_assignment.member_id if current_assignment else None

    if current_assignment:
        current_assignment.is_active = False
        current_assignment.unassigned_at = timezone.now()
        current_assignment.updated_by = actor
        current_assignment.save(
            update_fields=[
                "is_active",
                "unassigned_at",
                "updated_by",
                "updated_at",
            ]
        )

    assignment = NumberAssignment.objects.create(
        whatsapp_number=locked_number,
        member=member,
        created_by=actor,
        updated_by=actor,
    )

    schedule_number_assignment_changed_event(
        whatsapp_number=locked_number,
    )

    schedule_audit_event(
        category=AUDIT_CATEGORY_REGISTRY.WHATSAPP,
        action=AUDIT_ACTION_REGISTRY.ASSIGN,
        description="Número de WhatsApp asignado a un miembro.",
        actor=actor,
        company=locked_number.company,
        branch=locked_number.branch,
        target=assignment,
        metadata={
            "whatsapp_number_id": locked_number.id,
            "previous_assignment_id": previous_assignment_id,
            "previous_member_id": previous_member_id,
            "new_assignment_id": assignment.id,
            "new_member_id": member.id,
            "new_member_username": member.username,
        },
    )

    return assignment


@transaction.atomic
def unassign_whatsapp_number(whatsapp_number, actor):
    """
        DOCSTRING: Unassign WhatsApp Number

        Description:
        - End the current active member assignment for a WhatsApp number.
        - Preserve the assignment record as historical responsibility information.
        - Publish the unassigned state after successful transaction commit.
        - Record the unassignment operation in the centralized audit subsystem.

        Notes:
        - The WhatsApp number is locked while the active assignment is closed.
        - Historical assignment records are never deleted.
        - An explicit validation error is returned when no active assignment exists.
        - Realtime and audit events are never emitted for rolled-back transactions.
    """

    locked_number = WhatsAppNumber.objects.select_for_update().select_related(
        "company",
        "branch",
    ).get(id=whatsapp_number.id)

    assignment = NumberAssignment.objects.select_for_update().filter(
        whatsapp_number=locked_number,
        is_active=True,
    ).first()

    if assignment is None:
        raise ValidationError({
            "assignment": "Desasignación rechazada: el número no tiene una asignación activa."
        })

    previous_member_id = assignment.member_id

    assignment.is_active = False
    assignment.unassigned_at = timezone.now()
    assignment.updated_by = actor
    assignment.save(
        update_fields=[
            "is_active",
            "unassigned_at",
            "updated_by",
            "updated_at",
        ]
    )

    schedule_number_assignment_changed_event(
        whatsapp_number=locked_number,
    )

    schedule_audit_event(
        category=AUDIT_CATEGORY_REGISTRY.WHATSAPP,
        action=AUDIT_ACTION_REGISTRY.UNASSIGN,
        description="Número de WhatsApp desasignado de un miembro.",
        actor=actor,
        company=locked_number.company,
        branch=locked_number.branch,
        target=assignment,
        metadata={
            "whatsapp_number_id": locked_number.id,
            "assignment_id": assignment.id,
            "previous_member_id": previous_member_id,
            "unassigned_at": assignment.unassigned_at.isoformat(),
        },
    )

    return assignment


@transaction.atomic
def activate_whatsapp_monitoring(whatsapp_number, actor):
    """
        DOCSTRING: Activate WhatsApp Monitoring

        Description:
        - Activate monitored message capture for a configured WhatsApp number.
        - Establish the timestamp from which new monitored messages become eligible for persistence.
        - Record the monitoring activation in the centralized audit subsystem.

        Notes:
        - The WhatsApp number is locked while the monitoring lifecycle is changed.
        - The number, company, branch, and WhatsApp Business Account must remain active.
        - The number and WhatsApp Business Account must be connected.
        - The WhatsApp Business Account webhook must be configured.
        - Monitoring activation must not import historical messages.
        - monitoring_started_at defines the inclusive beginning of the current monitoring period.
        - Audit history is created only if the surrounding transaction commits successfully.
    """

    locked_number = WhatsAppNumber.objects.select_for_update().select_related(
        "company",
        "branch",
        "whatsapp_business_account",
    ).get(id=whatsapp_number.id)

    if not locked_number.is_active:
        raise ValidationError({
            "whatsapp_number": "Activación de monitoreo rechazada: el número de WhatsApp se encuentra inactivo."
        })

    if not locked_number.company.is_active:
        raise ValidationError({
            "company": "Activación de monitoreo rechazada: la empresa se encuentra inactiva."
        })

    if not locked_number.branch.is_active:
        raise ValidationError({
            "branch": "Activación de monitoreo rechazada: la sucursal se encuentra inactiva."
        })

    if not locked_number.whatsapp_business_account.is_active:
        raise ValidationError({
            "whatsapp_business_account": "Activación de monitoreo rechazada: la cuenta de WhatsApp Business se encuentra inactiva."
        })

    if not locked_number.whatsapp_business_account.is_connected:
        raise ValidationError({
            "whatsapp_business_account": "Activación de monitoreo rechazada: la cuenta de WhatsApp Business no se encuentra conectada."
        })

    if not locked_number.whatsapp_business_account.is_webhook_configured:
        raise ValidationError({
            "whatsapp_business_account": "Activación de monitoreo rechazada: el webhook de la cuenta de WhatsApp Business no se encuentra configurado."
        })

    if not locked_number.is_connected:
        raise ValidationError({
            "whatsapp_number": "Activación de monitoreo rechazada: el número de WhatsApp no se encuentra conectado."
        })

    if locked_number.is_monitoring_enabled:
        raise ValidationError({
            "whatsapp_number": "Activación de monitoreo rechazada: el monitoreo ya se encuentra activo."
        })

    locked_number.is_monitoring_enabled = True
    locked_number.monitoring_started_at = timezone.now()
    locked_number.monitoring_stopped_at = None
    locked_number.updated_by = actor
    locked_number.save(
        update_fields=[
            "is_monitoring_enabled",
            "monitoring_started_at",
            "monitoring_stopped_at",
            "updated_by",
            "updated_at",
        ]
    )

    schedule_audit_event(
        category=AUDIT_CATEGORY_REGISTRY.WHATSAPP,
        action=AUDIT_ACTION_REGISTRY.ACTIVATE,
        description="Monitoreo de número de WhatsApp activado.",
        actor=actor,
        company=locked_number.company,
        branch=locked_number.branch,
        target=locked_number,
        metadata={
            "whatsapp_number_id": locked_number.id,
            "monitoring_started_at": locked_number.monitoring_started_at.isoformat(),
        },
    )

    return locked_number


@transaction.atomic
def deactivate_whatsapp_monitoring(whatsapp_number, actor):
    """
        DOCSTRING: Deactivate WhatsApp Monitoring

        Description:
        - Stop monitored message capture for an active WhatsApp monitoring configuration.
        - Record the monitoring deactivation in the centralized audit subsystem.

        Notes:
        - The WhatsApp number is locked while the monitoring lifecycle is changed.
        - Previously captured messages remain preserved.
        - Messages received after monitoring_stopped_at must not become monitored history.
        - Deactivation does not disconnect the WhatsApp number from Meta.
        - Audit history is created only if the surrounding transaction commits successfully.
    """

    locked_number = WhatsAppNumber.objects.select_for_update().select_related(
        "company",
        "branch",
    ).get(id=whatsapp_number.id)

    if not locked_number.is_monitoring_enabled:
        raise ValidationError({
            "whatsapp_number": "Desactivación de monitoreo rechazada: el monitoreo no se encuentra activo."
        })

    locked_number.is_monitoring_enabled = False
    locked_number.monitoring_stopped_at = timezone.now()
    locked_number.updated_by = actor
    locked_number.save(
        update_fields=[
            "is_monitoring_enabled",
            "monitoring_stopped_at",
            "updated_by",
            "updated_at",
        ]
    )

    schedule_audit_event(
        category=AUDIT_CATEGORY_REGISTRY.WHATSAPP,
        action=AUDIT_ACTION_REGISTRY.DEACTIVATE,
        description="Monitoreo de número de WhatsApp desactivado.",
        actor=actor,
        company=locked_number.company,
        branch=locked_number.branch,
        target=locked_number,
        metadata={
            "whatsapp_number_id": locked_number.id,
            "monitoring_started_at": (
                locked_number.monitoring_started_at.isoformat()
                if locked_number.monitoring_started_at
                else None
            ),
            "monitoring_stopped_at": locked_number.monitoring_stopped_at.isoformat(),
        },
    )

    return locked_number


def is_message_relevant_for_unread(message):
    """
        DOCSTRING: Is Message Relevant For Unread

        Description:
        - Determine whether a persisted WhatsApp message should increment monitoring-user unread counters.

        Notes:
        - Only inbound customer messages may create unread state.
        - Outbound messages never increment monitor unread counters.
        - Reaction events do not increment unread counters.
        - Revoked messages do not increment unread counters.
        - Unsupported inbound messages remain relevant because monitors must still be made aware that customer activity occurred.
    """

    if message.direction != MESSAGE_DIRECTION_REGISTRY.INBOUND:
        return False

    if message.is_revoked:
        return False

    if message.message_type == MESSAGE_TYPE_REGISTRY.REACTION:
        return False

    return True


@transaction.atomic
def mark_conversation_as_read(conversation, user):
    """
        DOCSTRING: Mark Conversation As Read

        Description:
        - Mark a monitored WhatsApp conversation as read for a specific monitoring user.
        - Reset the authenticated user's unread counter.
        - Preserve the most recent message acknowledged by the monitoring user.

        Notes:
        - Read state belongs independently to each monitoring user.
        - The conversation row is locked to obtain a consistent latest-message reference.
        - The ConversationReadState row is locked independently when it already exists.
        - Nullable message relationships are not included in SELECT FOR UPDATE joins.
    """

    locked_conversation = Conversation.objects.select_for_update().get(
        id=conversation.id
    )

    last_message = locked_conversation.last_message

    read_state = ConversationReadState.objects.select_for_update().filter(
        conversation=locked_conversation,
        user=user,
    ).first()

    if read_state is None:
        read_state = ConversationReadState.objects.create(
            conversation=locked_conversation,
            user=user,
            is_read=True,
            unread_count=0,
            last_read_message=last_message,
            last_opened_at=timezone.now(),
        )

    else:
        read_state.is_read = True
        read_state.unread_count = 0
        read_state.last_read_message = last_message
        read_state.last_opened_at = timezone.now()

        read_state.save(
            update_fields=[
                "is_read",
                "unread_count",
                "last_read_message",
                "last_opened_at",
                "updated_at",
            ]
        )

    return read_state


@transaction.atomic
def mark_conversation_as_unread(conversation, message):
    """
        DOCSTRING: Mark Conversation As Unread

        Description:
        - Mark a conversation as unread for every authorized MONITOR and the currently assigned MEMBER.
        - Increment unread counters atomically for a relevant inbound message.
        - Publish each user's resulting read state after successful transaction commit.

        Notes:
        - MONITOR users are resolved through active company-access assignments.
        - MEMBER users are resolved through the active assignment of the conversation WhatsApp number.
        - Existing unread counters are incremented through database F expressions.
        - Duplicate users are collapsed before read-state updates are applied.
        - Automatic unread-counter changes are not audit events because they are system-generated message-ingestion state.
    """

    if not is_message_relevant_for_unread(message):
        return 0

    locked_conversation = Conversation.objects.select_for_update().select_related(
        "whatsapp_number__company"
    ).get(id=conversation.id)

    monitor_users = [
        company_access.user
        for company_access in UserCompanyAccess.objects.select_related("user").filter(
            company=locked_conversation.whatsapp_number.company,
            is_active=True,
            user__is_active=True,
            user__role=ROLE_REGISTRY.MONITOR,
        )
    ]

    member_users = [
        assignment.member
        for assignment in NumberAssignment.objects.select_related("member").filter(
            whatsapp_number=locked_conversation.whatsapp_number,
            is_active=True,
            unassigned_at__isnull=True,
            member__is_active=True,
            member__role=ROLE_REGISTRY.MEMBER,
        )
    ]

    users = {
        user.id: user
        for user in [*monitor_users, *member_users]
    }.values()

    updated_count = 0

    for user in users:
        affected_rows = ConversationReadState.objects.filter(
            conversation=locked_conversation,
            user=user,
        ).update(
            is_read=False,
            unread_count=F("unread_count") + 1,
        )

        if not affected_rows:
            try:
                ConversationReadState.objects.create(
                    conversation=locked_conversation,
                    user=user,
                    is_read=False,
                    unread_count=1,
                )
            except IntegrityError:
                ConversationReadState.objects.filter(
                    conversation=locked_conversation,
                    user=user,
                ).update(
                    is_read=False,
                    unread_count=F("unread_count") + 1,
                )

        read_state = ConversationReadState.objects.filter(
            conversation=locked_conversation,
            user=user,
        ).first()

        if read_state:
            schedule_conversation_read_state_changed_event(
                read_state=read_state,
            )

        updated_count += 1

    return updated_count


@transaction.atomic
def recalculate_conversation_read_state(conversation, user):
    """
        DOCSTRING: Recalculate Conversation Read State

        Description:
        - Recalculate the unread message counter for one monitoring user using persisted conversation history.
        - Repair a read-state record if its cached unread counter becomes inconsistent.

        Notes:
        - The conversation and read-state record are locked during recalculation.
        - Only relevant inbound messages are counted.
        - Messages at or before last_read_message are considered acknowledged.
        - Reaction messages and revoked messages are excluded from unread calculation.
        - Automatic read-state repair is not treated as a human audit action.
    """

    locked_conversation = Conversation.objects.select_for_update().get(
        id=conversation.id
    )

    read_state = ConversationReadState.objects.select_for_update().filter(
        conversation=locked_conversation,
        user=user,
    ).first()

    unread_messages = Message.objects.filter(
        conversation=locked_conversation,
        direction=MESSAGE_DIRECTION_REGISTRY.INBOUND,
        is_active=True,
        revoked_at__isnull=True,
    ).exclude(
        message_type=MESSAGE_TYPE_REGISTRY.REACTION
    )

    if read_state is None:
        unread_count = unread_messages.count()

        read_state = ConversationReadState.objects.create(
            conversation=locked_conversation,
            user=user,
            is_read=unread_count == 0,
            unread_count=unread_count,
        )

        return read_state

    if read_state.last_read_message:
        last_read_message = read_state.last_read_message

        unread_messages = unread_messages.filter(
            Q(
                message_timestamp__gt=last_read_message.message_timestamp
            )
            | Q(
                message_timestamp=last_read_message.message_timestamp,
                id__gt=last_read_message.id,
            )
        )

    unread_count = unread_messages.count()

    read_state.unread_count = unread_count
    read_state.is_read = unread_count == 0

    read_state.save(
        update_fields=[
            "unread_count",
            "is_read",
            "updated_at",
        ]
    )

    return read_state


def resolve_whatsapp_number(meta_phone_number_id):
    """
        DOCSTRING: Resolve WhatsApp Number

        Description:
        - Resolve an active corporate WhatsApp number from a Meta phone-number identifier.
        - Resolve tenant ownership from the globally unique Meta Phone Number ID.

        Notes:
        - meta_phone_number_id is globally unique in Dialoqo.
        - The number, company, branch, WABA, and company Meta connection must all remain active.
        - Company context is derived from the resolved number rather than from a webhook-specific integration key.
    """

    if not meta_phone_number_id:
        return None

    return WhatsAppNumber.objects.select_related(
        "company",
        "branch",
        "whatsapp_business_account",
        "whatsapp_business_account__company",
    ).filter(
        meta_phone_number_id=meta_phone_number_id,
        company__is_active=True,
        branch__is_active=True,
        whatsapp_business_account__is_active=True,
        is_active=True,
    ).first()


def is_message_inside_monitoring_window(
    whatsapp_number,
    message_timestamp,
):
    """
        DOCSTRING: Is Message Inside Monitoring Window

        Description:
        - Determine whether new message content is eligible for persistence under the current monitoring period.

        Notes:
        - New historical content preceding monitoring_started_at is rejected.
        - New content received after monitoring_stopped_at is rejected.
        - Status, edit, and revocation updates for already persisted messages are handled independently.
    """

    if (
        not whatsapp_number.is_monitoring_enabled
        or not whatsapp_number.monitoring_started_at
    ):
        return False

    if message_timestamp < whatsapp_number.monitoring_started_at:
        return False

    if (
        whatsapp_number.monitoring_stopped_at
        and message_timestamp > whatsapp_number.monitoring_stopped_at
    ):
        return False

    return True


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
def update_conversation_last_message(conversation, message):
    """
        DOCSTRING: Update Conversation Last Message

        Description:
        - Update the conversation preview only when the supplied message is chronologically equal to or newer than the current latest message.

        Notes:
        - Delayed webhook deliveries must not regress the conversation preview.
        - Edits and revocations modify the existing latest message record directly and therefore do not require chronological replacement.
    """

    locked_conversation = Conversation.objects.select_for_update().get(
        id=conversation.id
    )

    if (
        locked_conversation.last_message_at
        and message.message_timestamp < locked_conversation.last_message_at
    ):
        return locked_conversation

    locked_conversation.last_message = message
    locked_conversation.last_message_at = message.message_timestamp

    locked_conversation.save(
        update_fields=[
            "last_message",
            "last_message_at",
            "updated_at",
        ]
    )

    return locked_conversation


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
    """

    statuses = value.get("statuses") or []
    updated_count = 0
    ignored_count = 0

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

        if message is None or not changed:
            ignored_count += 1
        else:
            updated_count += 1

    return {
        "created": 0,
        "updated": updated_count,
        "ignored": ignored_count,
    }


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
    """

    created_count = 0
    updated_count = 0
    ignored_count = 0

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

    return {
        "created": created_count,
        "updated": updated_count,
        "ignored": ignored_count,
    }


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