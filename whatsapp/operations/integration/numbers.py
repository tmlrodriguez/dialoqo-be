"""Meta phone number identity and connection validation."""

import logging

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction

from auditing.operations import schedule_audit_event
from auditing.registry import AUDIT_ACTION_REGISTRY, AUDIT_CATEGORY_REGISTRY

from whatsapp.meta.lifecycle import (
    MetaLifecycleAPIError,
    log_meta_lifecycle_error,
)
from whatsapp.models import WhatsAppNumber
from whatsapp.operations.credentials import prepare_waba_connection_client, store_validated_meta_credential
from whatsapp.utils.webhooks import normalize_phone_number


logger = logging.getLogger(__name__)


def validate_phone_number_identity(whatsapp_number, meta_phone_data):
    """
        DOCSTRING: Validate Phone Number Identity

        Description:
        - Validate that the phone number returned by Meta matches
          the corporate number configured in Dialoqo.

        Notes:
        - Comparison ignores formatting characters and leading plus signs.
        - A mismatched phone number causes validation to fail.
        - Phone numbers are never printed in diagnostic logs.
    """

    meta_display_phone_number = (
        meta_phone_data.get("display_phone_number") or ""
    )

    configured_phone_number = whatsapp_number.phone_number

    matches = (
        normalize_phone_number(meta_display_phone_number)
        ==
        normalize_phone_number(configured_phone_number)
    )

    logger.info(
        "[META PHONE] IDENTITY CHECK | "
        "number_id=%s | meta_phone_number_id=%s | matches=%s",
        whatsapp_number.id,
        whatsapp_number.meta_phone_number_id,
        matches,
    )

    if not matches:
        raise ValidationError({
            "meta_phone_number_id":
                "Validación de número rechazada: el Phone Number ID "
                "corresponde a un número diferente al configurado."
        })

    return meta_phone_data


def find_waba_phone_number(waba_phone_numbers, meta_phone_number_id):
    """
        DOCSTRING: Find WABA Phone Number

        Description:
        - Resolve a Phone Number ID from the collection returned
          by the configured WABA.

        Notes:
        - Returning None means the Phone Number ID was not found.
        - Phone-number identifiers are compared as strings.
    """

    expected_id = str(meta_phone_number_id)

    for phone_data in waba_phone_numbers:
        if str(phone_data.get("id")) == expected_id:
            return phone_data

    return None


def validate_whatsapp_number_connection(
    whatsapp_number,
    actor,
    client=None,
    audit_event=True,
):
    """
        DOCSTRING: Validate WhatsApp Number Connection

        Description:
        - Validate a configured Meta Phone Number ID.
        - Confirm membership in the expected WABA.
        - Verify the configured phone-number identity.
        - Persist a missing manual WABA credential after successful Meta checks.
        - Synchronize the local connection state.
        - Generate structured diagnostic logs.

        Notes:
        - Meta API calls execute outside database transactions.
        - Database locks are acquired only while saving state.
        - Failed validation persists the disconnected state.
        - Internal validations may disable audit generation.
        - Existing credentials are never replaced by the global token.
        - Sensitive credentials are never logged.
    """

    current_number = WhatsAppNumber.objects.select_related(
        "company",
        "branch",
        "whatsapp_business_account",
    ).get(
        id=whatsapp_number.id
    )

    account = current_number.whatsapp_business_account

    logger.info(
        "[META PHONE] VALIDATION START | "
        "company_id=%s | "
        "waba_id=%s | "
        "number_id=%s | "
        "meta_phone_number_id=%s",
        current_number.company_id,
        account.meta_waba_id,
        current_number.id,
        current_number.meta_phone_number_id,
    )

    try:
        if current_number.company_id != account.company_id:
            raise ValidationError({
                "whatsapp_number":
                    "Validación de número rechazada: "
                    "la jerarquía de empresa y WABA no es consistente."
            })

        if current_number.branch.company_id != current_number.company_id:
            raise ValidationError({
                "branch":
                    "Validación de número rechazada: "
                    "la sucursal no pertenece a la empresa."
            })

        needs_credential = False
        if client is None:
            client, needs_credential = prepare_waba_connection_client(account)

        logger.info(
            "[META PHONE] FETCHING WABA NUMBERS | waba_id=%s",
            account.meta_waba_id,
        )

        waba_phone_numbers = client.list_waba_phone_numbers(
            account.meta_waba_id
        )

        logger.info(
            "[META PHONE] WABA NUMBERS RECEIVED | "
            "waba_id=%s | count=%s",
            account.meta_waba_id,
            len(waba_phone_numbers),
        )

        waba_phone_data = find_waba_phone_number(
            waba_phone_numbers=waba_phone_numbers,
            meta_phone_number_id=current_number.meta_phone_number_id,
        )

        if waba_phone_data is None:
            raise ValidationError({
                "meta_phone_number_id":
                    "Validación de número rechazada: "
                    "el Phone Number ID no pertenece a la WABA configurada."
            })

        logger.info(
            "[META PHONE] WABA MEMBERSHIP CONFIRMED | "
            "number_id=%s",
            current_number.id,
        )

        meta_phone_data = client.get_phone_number(
            current_number.meta_phone_number_id
        )

        if str(meta_phone_data.get("id")) != str(
            current_number.meta_phone_number_id
        ):
            raise ValidationError({
                "meta_phone_number_id":
                    "Validación de número rechazada: "
                    "Meta devolvió un Phone Number ID diferente."
            })

        validate_phone_number_identity(
            whatsapp_number=current_number,
            meta_phone_data=meta_phone_data,
        )

        if needs_credential:
            store_validated_meta_credential(
                account=account,
                access_token=client.access_token,
                replace_existing=False,
            )

    except (ImproperlyConfigured, MetaLifecycleAPIError, ValidationError) as error:

        log_meta_lifecycle_error(
            operation="validate_whatsapp_number_connection",
            error=error,
            company_id=current_number.company_id,
            number_id=current_number.id,
            waba_id=account.meta_waba_id,
        )

        with transaction.atomic():
            locked_number = WhatsAppNumber.objects.select_for_update().get(
                id=current_number.id
            )

            locked_number.is_connected = False
            locked_number.updated_by = actor

            locked_number.save(
                update_fields=[
                    "is_connected",
                    "updated_by",
                    "updated_at",
                ]
            )

        logger.warning(
            "[META PHONE] VALIDATION FAILED | "
            "number_id=%s | connected=False",
            current_number.id,
        )

        raise

    with transaction.atomic():

        locked_number = WhatsAppNumber.objects.select_for_update().select_related(
            "company",
            "branch",
            "whatsapp_business_account",
        ).get(
            id=current_number.id
        )

        locked_number.is_connected = True
        locked_number.updated_by = actor

        locked_number.save(
            update_fields=[
                "is_connected",
                "updated_by",
                "updated_at",
            ]
        )

        if audit_event:
            schedule_audit_event(
                category=AUDIT_CATEGORY_REGISTRY.META,
                action=AUDIT_ACTION_REGISTRY.VALIDATE,
                description="Número de WhatsApp validado correctamente contra Meta.",
                actor=actor,
                company=locked_number.company,
                branch=locked_number.branch,
                target=locked_number,
                metadata={
                    "whatsapp_number_id": locked_number.id,
                    "whatsapp_business_account_id":
                        locked_number.whatsapp_business_account_id,
                    "meta_waba_id":
                        locked_number.whatsapp_business_account.meta_waba_id,
                    "meta_phone_number_id":
                        locked_number.meta_phone_number_id,
                    "is_connected": True,
                    "verified_name":
                        meta_phone_data.get("verified_name") or "",
                    "code_verification_status":
                        meta_phone_data.get("code_verification_status") or "",
                },
            )

    logger.info(
        "[META PHONE] VALIDATION COMPLETED | "
        "number_id=%s | connected=True",
        locked_number.id,
    )

    return {
        "whatsapp_number": locked_number,
        "meta_phone": meta_phone_data,
    }
