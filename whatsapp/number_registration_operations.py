import logging

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from auditing.operations import schedule_audit_event
from auditing.registry import (
    AUDIT_ACTION_REGISTRY,
    AUDIT_CATEGORY_REGISTRY,
)

from organizations.models import Branch

from .models import WhatsAppNumber
from .webhook import normalize_phone_number


logger = logging.getLogger(__name__)


# Define your WhatsApp number registration operations here.


def get_default_company_branch(company):
    """
        DOCSTRING: Get Default Company Branch

        Description:
        - Resolve the default Principal branch belonging
          to the supplied company.

        Notes:
        - The branch must already exist.
        - WhatsApp onboarding never creates branches.
        - Inactive branches cannot receive new numbers.
    """

    branch = Branch.objects.filter(
        company=company,
        name="Principal",
        is_active=True,
    ).order_by("id").first()

    if branch is None:
        raise ValidationError({
            "branch":
                "La empresa no tiene una sucursal Principal activa."
        })

    return branch


def validate_discovered_phone_number(phone_data):
    """
        DOCSTRING: Validate Discovered Phone Number

        Description:
        - Validate and normalize a phone number
          discovered through Meta Embedded Signup.

        Notes:
        - Meta Phone Number ID is mandatory.
        - The phone number must be valid.
        - Customer phone numbers are never logged.
    """

    if not isinstance(phone_data, dict):
        raise ValidationError({
            "phone_number":
                "Meta devolvió información de número inválida."
        })

    meta_phone_number_id = str(
        phone_data.get("id") or ""
    ).strip()

    display_phone_number = (
        phone_data.get("display_phone_number") or ""
    )

    verified_name = (
        phone_data.get("verified_name") or ""
    )

    if not meta_phone_number_id:
        raise ValidationError({
            "meta_phone_number_id":
                "Meta no proporcionó el Phone Number ID."
        })

    normalized_number = normalize_phone_number(
        display_phone_number
    )

    if not normalized_number:
        raise ValidationError({
            "phone_number":
                "Meta devolvió un número telefónico inválido."
        })

    if len(normalized_number) > 30:
        raise ValidationError({
            "phone_number":
                "El número telefónico excede la longitud permitida."
        })

    display_name = (
        str(verified_name).strip()
        or normalized_number
    )[:150]

    return {
        "meta_phone_number_id": meta_phone_number_id,
        "phone_number": normalized_number,
        "display_name": display_name,
    }


def register_discovered_whatsapp_numbers(
    company,
    actor,
    whatsapp_business_account,
    phone_numbers,
):
    """
        DOCSTRING: Register Discovered WhatsApp Numbers

        Description:
        - Register WhatsApp numbers discovered through
          Meta Embedded Signup.
        - Assign newly registered numbers to the company's
          default Principal branch.
        - Preserve existing number configurations.
        - Prevent cross-company reassignment.
        - Record centralized audit events.

        Notes:
        - The WABA must belong to the supplied company.
        - Existing numbers are reused idempotently.
        - Existing branch assignments are preserved.
        - Existing MEMBER assignments are preserved.
        - Monitoring is never activated automatically.
        - New numbers inherit the validated WABA connection state.
        - Database modifications are transactional.
    """

    if not company or not company.is_active:
        raise ValidationError({
            "company":
                "La empresa no está disponible."
        })

    if not actor or not actor.is_active:
        raise ValidationError({
            "actor":
                "El administrador no está activo."
        })

    if not whatsapp_business_account:
        raise ValidationError({
            "whatsapp_business_account":
                "La cuenta WABA es obligatoria."
        })

    if whatsapp_business_account.company_id != company.id:
        raise ValidationError({
            "whatsapp_business_account":
                "La WABA no pertenece a la empresa."
        })

    if not whatsapp_business_account.is_active:
        raise ValidationError({
            "whatsapp_business_account":
                "La cuenta WABA está desactivada."
        })

    if not isinstance(phone_numbers, list):
        raise ValidationError({
            "phone_numbers":
                "La lista de números descubiertos no es válida."
        })

    logger.info(
        "[WHATSAPP REGISTRATION] START | "
        "company_id=%s | account_id=%s | "
        "discovered_numbers=%s",
        company.id,
        whatsapp_business_account.id,
        len(phone_numbers),
    )

    branch = get_default_company_branch(
        company=company
    )

    registered_numbers = []

    with transaction.atomic():

        for phone_data in phone_numbers:

            normalized_data = validate_discovered_phone_number(
                phone_data
            )

            meta_phone_number_id = (
                normalized_data["meta_phone_number_id"]
            )

            phone_number = (
                normalized_data["phone_number"]
            )

            existing_number = (
                WhatsAppNumber.objects
                .select_for_update()
                .filter(
                    meta_phone_number_id=meta_phone_number_id
                )
                .first()
            )

            if existing_number:

                if existing_number.company_id != company.id:
                    raise ValidationError({
                        "phone_number":
                            "El número ya pertenece a otra empresa."
                    })

                if (
                    existing_number.whatsapp_business_account_id
                    != whatsapp_business_account.id
                ):
                    raise ValidationError({
                        "whatsapp_business_account":
                            "El número pertenece a otra WABA."
                    })

                if not existing_number.is_active:
                    raise ValidationError({
                        "phone_number":
                            "El número existe, pero está desactivado."
                    })

                if (
                    normalize_phone_number(existing_number.phone_number)
                    != phone_number
                ):
                    raise ValidationError({
                        "phone_number":
                            "La identidad del número no coincide "
                            "con el registro existente."
                    })

                logger.info(
                    "[WHATSAPP REGISTRATION] EXISTING NUMBER REUSED | "
                    "number_id=%s | account_id=%s",
                    existing_number.id,
                    whatsapp_business_account.id,
                )

                registered_numbers.append({
                    "whatsapp_number": existing_number,
                    "created": False,
                })

                continue

            conflicting_number = (
                WhatsAppNumber.objects
                .select_for_update()
                .filter(
                    phone_number=phone_number
                )
                .first()
            )

            if conflicting_number:
                raise ValidationError({
                    "phone_number":
                        "El número ya está registrado en Dialoqo "
                        "con otro identificador de Meta."
                })

            number = WhatsAppNumber.objects.create(
                company=company,
                branch=branch,
                whatsapp_business_account=whatsapp_business_account,
                phone_number=phone_number,
                display_name=normalized_data["display_name"],
                meta_phone_number_id=meta_phone_number_id,
                is_connected=whatsapp_business_account.is_connected,
                is_monitoring_enabled=False,
                monitoring_started_at=None,
                monitoring_stopped_at=None,
                created_by=actor,
                updated_by=actor,
            )

            schedule_audit_event(
                category=AUDIT_CATEGORY_REGISTRY.WHATSAPP,
                action=AUDIT_ACTION_REGISTRY.CREATE,
                description=(
                    "Número de WhatsApp registrado "
                    "mediante Meta Embedded Signup."
                ),
                actor=actor,
                company=company,
                branch=branch,
                target=number,
                metadata={
                    "source": "EMBEDDED_SIGNUP",
                    "whatsapp_number_id": number.id,
                    "whatsapp_business_account_id":
                        whatsapp_business_account.id,
                    "meta_phone_number_id":
                        meta_phone_number_id,
                    "is_connected":
                        number.is_connected,
                    "is_monitoring_enabled": False,
                },
            )

            logger.info(
                "[WHATSAPP REGISTRATION] NUMBER CREATED | "
                "number_id=%s | company_id=%s | "
                "branch_id=%s | account_id=%s",
                number.id,
                company.id,
                branch.id,
                whatsapp_business_account.id,
            )

            registered_numbers.append({
                "whatsapp_number": number,
                "created": True,
            })

    created_count = sum(
        1
        for item in registered_numbers
        if item["created"]
    )

    logger.info(
        "[WHATSAPP REGISTRATION] COMPLETED | "
        "company_id=%s | account_id=%s | "
        "total=%s | created=%s | reused=%s",
        company.id,
        whatsapp_business_account.id,
        len(registered_numbers),
        created_count,
        len(registered_numbers) - created_count,
    )

    return {
        "numbers": registered_numbers,
        "created_count": created_count,
        "reused_count": (
            len(registered_numbers) - created_count
        ),
    }