"""Number assignment validation and lifecycle workflows."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from access.registry import ROLE_REGISTRY
from auditing.operations import schedule_audit_event
from auditing.registry import AUDIT_ACTION_REGISTRY, AUDIT_CATEGORY_REGISTRY

from whatsapp.models import NumberAssignment, WhatsAppNumber
from whatsapp.realtime.events import schedule_number_assignment_changed_event


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
        - Assign an available Dialoqo MEMBER user to an unassigned WhatsApp number.
        - Preserve the complete historical responsibility chain.
        - Publish the new assignment state after successful transaction commit.
        - Record the assignment operation in the centralized audit subsystem.

        Notes:
        - Assignment history must always remain preserved.
        - The number and member are locked in that order to serialize concurrent assignment operations.
        - Joined company and branch records are not locked.
        - The assigned user must be an eligible active MEMBER owned by the company administrator.
        - Both the number and member must have no active assignment, including assignments in other companies.
        - Occupied numbers are never transferred automatically; explicit unassignment is required first.
        - Database uniqueness conflicts are returned as assignment validation errors.
        - Realtime and audit events are never emitted for rolled-back transactions.
    """

    locked_number = WhatsAppNumber.objects.select_for_update(of=("self",)).select_related(
        "company",
        "branch",
    ).get(id=whatsapp_number.id)

    locked_member = get_user_model().objects.select_for_update(of=("self",)).get(id=member.id)

    validate_whatsapp_number_assignment_member(
        whatsapp_number=locked_number,
        member=locked_member,
    )

    conflict_errors = {
        "unique_active_whatsapp_number_assignment": {
            "whatsapp_number": "Asignación de número rechazada: el número ya tiene un miembro asignado. Desasígnelo antes de volver a asignarlo.",
        },
        "unique_active_whatsapp_member_assignment": {
            "member": "Asignación de número rechazada: el miembro ya tiene un número asignado. Desasígnelo antes de asignarle otro.",
        },
    }

    if NumberAssignment.objects.filter(whatsapp_number=locked_number, is_active=True).exists():
        raise ValidationError(conflict_errors["unique_active_whatsapp_number_assignment"])

    if NumberAssignment.objects.filter(member=locked_member, is_active=True).exists():
        raise ValidationError(conflict_errors["unique_active_whatsapp_member_assignment"])

    try:
        with transaction.atomic():
            assignment = NumberAssignment.objects.create(
                whatsapp_number=locked_number,
                member=locked_member,
                created_by=actor,
                updated_by=actor,
            )
    except IntegrityError as error:
        constraint_name = getattr(getattr(error.__cause__, "diag", None), "constraint_name", None)
        if constraint_name not in conflict_errors:
            raise
        raise ValidationError(conflict_errors[constraint_name]) from error

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
            "previous_assignment_id": None,
            "previous_member_id": None,
            "new_assignment_id": assignment.id,
            "new_member_id": locked_member.id,
            "new_member_username": locked_member.username,
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

    locked_number = WhatsAppNumber.objects.select_for_update(of=("self",)).select_related(
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
