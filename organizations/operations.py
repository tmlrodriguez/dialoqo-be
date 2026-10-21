from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from access.registry import ROLE_REGISTRY
from auditing.operations import schedule_audit_event
from auditing.registry import (
    AUDIT_ACTION_REGISTRY,
    AUDIT_CATEGORY_REGISTRY,
)

from .models import Branch, Company, UserCompanyAccess


# ============================================================
# COMPANY OPERATIONS
# ============================================================


@transaction.atomic
def create_company(validated_data, actor):
    """
        DOCSTRING: Create Company

        Description:
        - Create a Dialoqo company owned by the authenticated administrative user.
        - Establish the company as a new tenant boundary.
        - Automatically create a default branch named "Principal".
        - Record company and branch creation through the centralized auditing subsystem.

        Notes:
        - The actor is persisted through the author fields.
        - Every new company receives one default branch.
        - Company and branch creation occur in the same database transaction.
        - If either operation fails, the entire transaction is rolled back.
        - Audit events are persisted only after the transaction commits successfully.
    """

    company = Company.objects.create(
        created_by=actor,
        updated_by=actor,
        **validated_data,
    )

    schedule_audit_event(
        category=AUDIT_CATEGORY_REGISTRY.ORGANIZATION,
        action=AUDIT_ACTION_REGISTRY.CREATE,
        description="Empresa creada en Dialoqo.",
        actor=actor,
        company=company,
        target=company,
        metadata={
            "company_id": company.id,
            "name": company.name,
            "is_active": company.is_active,
        },
    )

    create_branch(
        company=company,
        validated_data={
            "name": "Principal",
        },
        actor=actor,
    )

    return company


@transaction.atomic
def update_company(company, validated_data, actor):
    """
        DOCSTRING: Update Company

        Description:
        - Apply validated changes to an existing Dialoqo company.
        - Record the administrative configuration change through the centralized auditing subsystem.

        Notes:
        - Only supplied fields are modified.
        - changed_fields identifies which company attributes were submitted for modification.
        - Previous and current values are stored only for non-sensitive organizational configuration.
        - The audit event is persisted only after the transaction commits successfully.
    """

    locked_company = Company.objects.select_for_update().get(
        id=company.id
    )

    changed_fields = list(validated_data.keys())

    previous_values = {
        field: getattr(locked_company, field)
        for field in changed_fields
    }

    for field, value in validated_data.items():
        setattr(locked_company, field, value)

    locked_company.updated_by = actor
    locked_company.save()

    current_values = {
        field: getattr(locked_company, field)
        for field in changed_fields
    }

    schedule_audit_event(
        category=AUDIT_CATEGORY_REGISTRY.ORGANIZATION,
        action=AUDIT_ACTION_REGISTRY.UPDATE,
        description="Empresa actualizada en Dialoqo.",
        actor=actor,
        company=locked_company,
        target=locked_company,
        metadata={
            "company_id": locked_company.id,
            "changed_fields": changed_fields,
            "previous_values": previous_values,
            "current_values": current_values,
        },
    )

    return locked_company


@transaction.atomic
def deactivate_company(company, actor):
    """
        DOCSTRING: Deactivate Company

        Description:
        - Deactivate an existing Dialoqo company without deleting historical information.
        - Record the company lifecycle change through the centralized auditing subsystem.

        Notes:
        - Historical organizational records remain preserved.
        - The operation is rejected when the company is already inactive.
        - Associated resource deactivation remains the responsibility of their corresponding subsystems.
    """

    locked_company = Company.objects.select_for_update().get(
        id=company.id
    )

    if not locked_company.is_active:
        raise ValidationError({
            "company": (
                "Desactivación de empresa rechazada: "
                "la empresa ya se encuentra inactiva."
            )
        })

    locked_company.is_active = False
    locked_company.updated_by = actor

    locked_company.save(
        update_fields=[
            "is_active",
            "updated_by",
            "updated_at",
        ]
    )

    schedule_audit_event(
        category=AUDIT_CATEGORY_REGISTRY.ORGANIZATION,
        action=AUDIT_ACTION_REGISTRY.DEACTIVATE,
        description="Empresa desactivada en Dialoqo.",
        actor=actor,
        company=locked_company,
        target=locked_company,
        metadata={
            "company_id": locked_company.id,
            "name": locked_company.name,
            "is_active": locked_company.is_active,
        },
    )

    return locked_company


# ============================================================
# BRANCH OPERATIONS
# ============================================================


@transaction.atomic
def create_branch(company, validated_data, actor):
    """
        DOCSTRING: Create Branch

        Description:
        - Create a branch inside an existing Dialoqo company.
        - Preserve the explicit company tenant boundary.
        - Record the branch creation through the centralized auditing subsystem.

        Notes:
        - Company ownership is resolved before this operation is called.
        - The company field is controlled by the backend.
        - The audit event includes both company and branch context.
        - This operation is also used to create the default branch
          when a new company is registered.
    """

    branch_data = dict(validated_data)
    branch_data.pop("company", None)

    branch = Branch.objects.create(
        company=company,
        created_by=actor,
        updated_by=actor,
        **branch_data,
    )

    schedule_audit_event(
        category=AUDIT_CATEGORY_REGISTRY.ORGANIZATION,
        action=AUDIT_ACTION_REGISTRY.CREATE,
        description="Sucursal creada en Dialoqo.",
        actor=actor,
        company=company,
        branch=branch,
        target=branch,
        metadata={
            "branch_id": branch.id,
            "company_id": company.id,
            "name": branch.name,
            "is_active": branch.is_active,
        },
    )

    return branch


@transaction.atomic
def update_branch(branch, validated_data, actor):
    """
        DOCSTRING: Update Branch

        Description:
        - Apply validated changes to an existing company branch.
        - Record the organizational configuration change through the centralized auditing subsystem.

        Notes:
        - The branch remains attached to its existing company.
        - The client cannot move a branch between companies through this operation.
        - Previous and current non-sensitive configuration values are preserved for traceability.
    """

    locked_branch = (
        Branch.objects
        .select_for_update()
        .select_related("company")
        .get(id=branch.id)
    )

    branch_data = dict(validated_data)
    branch_data.pop("company", None)

    changed_fields = list(branch_data.keys())

    previous_values = {
        field: getattr(locked_branch, field)
        for field in changed_fields
    }

    for field, value in branch_data.items():
        setattr(locked_branch, field, value)

    locked_branch.updated_by = actor
    locked_branch.save()

    current_values = {
        field: getattr(locked_branch, field)
        for field in changed_fields
    }

    schedule_audit_event(
        category=AUDIT_CATEGORY_REGISTRY.ORGANIZATION,
        action=AUDIT_ACTION_REGISTRY.UPDATE,
        description="Sucursal actualizada en Dialoqo.",
        actor=actor,
        company=locked_branch.company,
        branch=locked_branch,
        target=locked_branch,
        metadata={
            "branch_id": locked_branch.id,
            "company_id": locked_branch.company_id,
            "changed_fields": changed_fields,
            "previous_values": previous_values,
            "current_values": current_values,
        },
    )

    return locked_branch


@transaction.atomic
def deactivate_branch(branch, actor):
    """
        DOCSTRING: Deactivate Branch

        Description:
        - Deactivate a Dialoqo branch without deleting its historical information.
        - Record the lifecycle change through the centralized auditing subsystem.

        Notes:
        - Historical references remain preserved.
        - The operation is rejected when the branch is already inactive.
    """

    locked_branch = (
        Branch.objects
        .select_for_update()
        .select_related("company")
        .get(id=branch.id)
    )

    if not locked_branch.is_active:
        raise ValidationError({
            "branch": (
                "Desactivación de sucursal rechazada: "
                "la sucursal ya se encuentra inactiva."
            )
        })

    locked_branch.is_active = False
    locked_branch.updated_by = actor

    locked_branch.save(
        update_fields=[
            "is_active",
            "updated_by",
            "updated_at",
        ]
    )

    schedule_audit_event(
        category=AUDIT_CATEGORY_REGISTRY.ORGANIZATION,
        action=AUDIT_ACTION_REGISTRY.DEACTIVATE,
        description="Sucursal desactivada en Dialoqo.",
        actor=actor,
        company=locked_branch.company,
        branch=locked_branch,
        target=locked_branch,
        metadata={
            "branch_id": locked_branch.id,
            "company_id": locked_branch.company_id,
            "name": locked_branch.name,
            "is_active": locked_branch.is_active,
        },
    )

    return locked_branch


# ============================================================
# USER COMPANY ACCESS OPERATIONS
# ============================================================


@transaction.atomic
def grant_user_company_access(user, company, actor):
    """
        DOCSTRING: Grant User Company Access

        Description:
        - Grant an active Dialoqo MONITOR explicit access to an active company owned by the authenticated administrator.
        - Preserve company authorization independently from user creation.
        - Record the access grant through the centralized auditing subsystem.

        Notes:
        - The user must be an active MONITOR.
        - The monitor must have been created by the authenticated administrator.
        - The company must have been created by the authenticated administrator.
        - Only one active access record may exist for the same user and company.
        - Revoked historical access records remain preserved.
    """

    if user.role != ROLE_REGISTRY.MONITOR:
        raise ValidationError({
            "user": (
                "Asignación de acceso rechazada: "
                "el usuario debe tener el rol MONITOR."
            )
        })

    if not user.is_active:
        raise ValidationError({
            "user": (
                "Asignación de acceso rechazada: "
                "el monitor seleccionado se encuentra inactivo."
            )
        })

    if user.created_by_id != actor.id:
        raise ValidationError({
            "user": (
                "Asignación de acceso rechazada: "
                "el monitor seleccionado no pertenece "
                "al administrador autenticado."
            )
        })

    if not company.is_active:
        raise ValidationError({
            "company": (
                "Asignación de acceso rechazada: "
                "la empresa seleccionada se encuentra inactiva."
            )
        })

    if company.created_by_id != actor.id:
        raise ValidationError({
            "company": (
                "Asignación de acceso rechazada: "
                "la empresa seleccionada no pertenece "
                "al administrador autenticado."
            )
        })

    if UserCompanyAccess.objects.filter(
        user=user,
        company=company,
        is_active=True,
    ).exists():
        raise ValidationError({
            "access": (
                "Asignación de acceso rechazada: "
                "el usuario ya tiene acceso activo a esta empresa."
            )
        })

    access = UserCompanyAccess.objects.create(
        user=user,
        company=company,
        created_by=actor,
        updated_by=actor,
    )

    schedule_audit_event(
        category=AUDIT_CATEGORY_REGISTRY.ORGANIZATION,
        action=AUDIT_ACTION_REGISTRY.GRANT,
        description="Acceso de monitor a empresa otorgado.",
        actor=actor,
        company=company,
        target=access,
        metadata={
            "user_company_access_id": access.id,
            "user_id": user.id,
            "username": user.username,
            "role": user.role,
            "company_id": company.id,
            "granted_at": access.granted_at.isoformat(),
            "is_active": access.is_active,
        },
    )

    return access


@transaction.atomic
def revoke_user_company_access(access, actor):
    """
        DOCSTRING: Revoke User Company Access

        Description:
        - Revoke an active monitor-company authorization owned by the authenticated administrator.
        - Preserve the historical access record.
        - Record the authorization change through the centralized auditing subsystem.

        Notes:
        - The monitor and company must belong to the authenticated administrator.
        - Revoked access records remain available for historical traceability.
        - Access revocation immediately removes the monitor's authorization for the company.
    """

    locked_access = (
        UserCompanyAccess.objects
        .select_for_update()
        .select_related("user", "company")
        .get(id=access.id)
    )

    if locked_access.company.created_by_id != actor.id:
        raise ValidationError({
            "company": (
                "Revocación de acceso rechazada: "
                "la empresa no pertenece al administrador autenticado."
            )
        })

    if locked_access.user.created_by_id != actor.id:
        raise ValidationError({
            "user": (
                "Revocación de acceso rechazada: "
                "el monitor no pertenece al administrador autenticado."
            )
        })

    if not locked_access.is_active:
        raise ValidationError({
            "access": (
                "Revocación de acceso rechazada: "
                "el acceso ya se encuentra inactivo."
            )
        })

    locked_access.is_active = False
    locked_access.revoked_at = timezone.now()
    locked_access.updated_by = actor

    locked_access.save(
        update_fields=[
            "is_active",
            "revoked_at",
            "updated_by",
            "updated_at",
        ]
    )

    schedule_audit_event(
        category=AUDIT_CATEGORY_REGISTRY.ORGANIZATION,
        action=AUDIT_ACTION_REGISTRY.REVOKE,
        description="Acceso de monitor a empresa revocado.",
        actor=actor,
        company=locked_access.company,
        target=locked_access,
        metadata={
            "user_company_access_id": locked_access.id,
            "user_id": locked_access.user_id,
            "username": locked_access.user.username,
            "role": locked_access.user.role,
            "company_id": locked_access.company_id,
            "revoked_at": locked_access.revoked_at.isoformat(),
            "is_active": locked_access.is_active,
        },
    )

    return locked_access