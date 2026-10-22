"""Number monitoring lifecycle and ingestion eligibility."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from auditing.operations import schedule_audit_event
from auditing.registry import AUDIT_ACTION_REGISTRY, AUDIT_CATEGORY_REGISTRY

from whatsapp.models import WhatsAppNumber


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
