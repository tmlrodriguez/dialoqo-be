"""Embedded Signup onboarding and local account registration."""

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from auditing.operations import schedule_audit_event
from auditing.registry import AUDIT_ACTION_REGISTRY, AUDIT_CATEGORY_REGISTRY

from whatsapp.meta.embedded_signup import (
    MetaEmbeddedSignupError,
    exchange_embedded_signup_authorization_code,
    get_embedded_signup_phone_numbers,
    resolve_embedded_signup_business_id,
    subscribe_embedded_signup_waba,
    validate_embedded_signup_operational_access,
    validate_embedded_signup_phone_number,
    validate_embedded_signup_token,
    validate_embedded_signup_waba,
)
from whatsapp.models import WhatsAppBusinessAccount
from whatsapp.operations.credentials import store_validated_meta_credential
from whatsapp.operations.number_registration import register_discovered_whatsapp_numbers


logger = logging.getLogger(__name__)


def register_embedded_signup_waba(
    company,
    actor,
    meta_waba,
    meta_business_id,
):
    """
        DOCSTRING: Register Embedded Signup WABA

        Description:
        - Create or reuse a WABA belonging to the selected company.
        - Preserve tenant ownership.

        Notes:
        - WABAs cannot be reassigned between companies.
        - Existing inactive WABAs are rejected.
    """

    meta_waba_id = str(
        meta_waba["id"]
    )

    with transaction.atomic():

        existing_account = (
            WhatsAppBusinessAccount.objects
            .select_for_update()
            .filter(
                meta_waba_id=meta_waba_id
            )
            .first()
        )

        if existing_account:

            if existing_account.company_id != company.id:
                raise ValidationError({
                    "waba_id":
                        "La WABA pertenece a otra empresa."
                })

            if not existing_account.is_active:
                raise ValidationError({
                    "waba_id":
                        "La WABA está desactivada."
                })

            if str(
                existing_account.meta_business_id
            ) != str(meta_business_id):
                raise ValidationError({
                    "business_id":
                        "El Business Portfolio ID no coincide."
                })

            return existing_account, False

        account = WhatsAppBusinessAccount.objects.create(
            company=company,
            meta_waba_id=meta_waba_id,
            meta_business_id=str(meta_business_id),
            display_name=(
                                meta_waba.get("name")
                or f"WhatsApp Business {meta_waba_id}"
            )[:150],
            is_connected=False,
            is_webhook_configured=False,
            created_by=actor,
            updated_by=actor,
        )

        schedule_audit_event(
            category=AUDIT_CATEGORY_REGISTRY.WHATSAPP,
            action=AUDIT_ACTION_REGISTRY.CREATE,
            description=(
                "Cuenta WABA registrada mediante Embedded Signup."
            ),
            actor=actor,
            company=company,
            target=account,
            metadata={
                "whatsapp_business_account_id": account.id,
                "meta_waba_id": meta_waba_id,
                "source": "EMBEDDED_SIGNUP",
            },
        )

    return account, True


def store_embedded_signup_credential(
    account,
    access_token,
    expires_at=None,
):
    """
        DOCSTRING: Store Embedded Signup Credential

        Description:
        - Encrypt and persist an authorized Meta access token.
        - Activate the credential after successful validation.
        - Preserve the expiration date returned by Meta.

        Notes:
        - The caller must validate the token before invoking
          this operation.
        - Plaintext tokens are never persisted.
        - Newly stored credentials initially remain inactive.
        - Credential activation occurs within the database transaction.
        - Access tokens are never logged or included in audit events.
    """

    logger.info(
        "[EMBEDDED SIGNUP] CREDENTIAL STORAGE START | "
        "account_id=%s",
        account.id,
    )

    credential = store_validated_meta_credential(
        account=account,
        access_token=access_token,
        expires_at=expires_at,
    )

    logger.info(
        "[EMBEDDED SIGNUP] CREDENTIAL STORED | "
        "account_id=%s | credential_id=%s | "
        "active=True | expiration_known=%s",
        account.id,
        credential.id,
        expires_at is not None,
    )

    return credential


def complete_whatsapp_embedded_signup(
    company,
    actor,
    authorization_code,
    meta_waba_id,
    meta_phone_number_id,
    meta_business_id=None,
):
    """
        DOCSTRING: Complete WhatsApp Embedded Signup

        Description:
        - Exchange the Meta authorization code.
        - Validate the token through Meta debug_token.
        - Verify application identity and required permissions.
        - Validate token expiration and WABA access.
        - Resolve Business Portfolio ownership.
        - Discover and validate WhatsApp phone numbers.
        - Subscribe Dialoqo to WABA webhook events.
        - Register or reuse the WABA.
        - Encrypt and persist the operational credential.
        - Register the selected WhatsApp number under Principal.
        - Synchronize connection state.
        - Record centralized audit events.

        Notes:
        - Company authorization must be enforced by the API view.
        - Authorization codes and access tokens are never logged.
        - The frontend Business Portfolio ID is not authoritative.
        - Only the selected number is registered automatically.
        - Existing number assignments and branches are preserved.
        - Monitoring is not activated automatically.
        - Coexistence requires subsequent messaging tests.
        - External Meta operations cannot be rolled back by Django.
    """

    if not company or not company.is_active:
        raise ValidationError({
            "company": "La empresa no está disponible."
        })

    if not actor or not actor.is_active:
        raise ValidationError({
            "actor": "El usuario administrador no está activo."
        })

    logger.info(
        "[EMBEDDED SIGNUP] ONBOARDING START | "
        "company_id=%s | actor_id=%s | "
        "waba_id=%s | phone_number_id=%s",
        company.id,
        actor.id,
        meta_waba_id,
        meta_phone_number_id,
    )

    try:

        # ---------------------------------------------------------
        # STEP 1: Exchange authorization code
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP] STEP 1 | "
            "Exchanging authorization code"
        )

        access_token = exchange_embedded_signup_authorization_code(
            authorization_code
        )

        # ---------------------------------------------------------
        # STEP 2: Validate token permissions and expiration
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP] STEP 2 | "
            "Validating Meta authorization"
        )

        token_validation = validate_embedded_signup_token(
            access_token=access_token,
            meta_waba_id=meta_waba_id,
        )

        token_expires_at = token_validation.get(
            "expires_at"
        )

        logger.info(
            "[EMBEDDED SIGNUP] TOKEN AUTHORIZATION VERIFIED | "
            "waba_id=%s | expiration_known=%s",
            meta_waba_id,
            token_expires_at is not None,
        )

        # ---------------------------------------------------------
        # STEP 3: Validate authorized WABA
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP] STEP 3 | "
            "Validating authorized WABA"
        )

        meta_waba = validate_embedded_signup_waba(
            access_token=access_token,
            meta_waba_id=meta_waba_id,
        )

        # ---------------------------------------------------------
        # STEP 4: Resolve Business Portfolio ownership
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP] STEP 4 | "
            "Resolving Business Portfolio ownership"
        )

        resolved_business_id = resolve_embedded_signup_business_id(
            meta_waba=meta_waba
        )

        if (
            meta_business_id
            and str(meta_business_id) != str(resolved_business_id)
        ):
            raise ValidationError({
                "business_id":
                    "El Business Portfolio ID proporcionado "
                    "no coincide con el propietario verificado en Meta."
            })

        logger.info(
            "[EMBEDDED SIGNUP] BUSINESS OWNERSHIP VERIFIED | "
            "waba_id=%s | business_id=%s",
            meta_waba_id,
            resolved_business_id,
        )

        # ---------------------------------------------------------
        # STEP 5: Discover authorized phone numbers
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP] STEP 5 | "
            "Discovering authorized phone numbers"
        )

        phone_numbers = get_embedded_signup_phone_numbers(
            access_token=access_token,
            meta_waba_id=meta_waba_id,
        )

        # ---------------------------------------------------------
        # STEP 6: Validate selected Phone Number ID
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP] STEP 6 | "
            "Validating selected Phone Number ID"
        )

        selected_phone = validate_embedded_signup_phone_number(
            phone_numbers=phone_numbers,
            meta_phone_number_id=meta_phone_number_id,
        )

        # ---------------------------------------------------------
        # STEP 7: Validate operational WABA access
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP] STEP 7 | "
            "Validating operational WABA access"
        )

        validate_embedded_signup_operational_access(
            access_token=access_token,
            meta_waba_id=meta_waba_id,
        )

        # ---------------------------------------------------------
        # STEP 8: Subscribe Dialoqo to Meta webhooks
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP] STEP 8 | "
            "Subscribing application to WABA"
        )

        subscribe_embedded_signup_waba(
            access_token=access_token,
            meta_waba_id=meta_waba_id,
        )

        # ---------------------------------------------------------
        # STEP 9: Register WABA, credential, and selected number
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP] STEP 9 | "
            "Registering WABA, credential, and selected number"
        )

        now = timezone.now()

        with transaction.atomic():

            account, created = register_embedded_signup_waba(
                company=company,
                actor=actor,
                meta_waba=meta_waba,
                meta_business_id=resolved_business_id,
            )

            credential = store_embedded_signup_credential(
                account=account,
                access_token=access_token,
                expires_at=token_expires_at,
            )

            # -----------------------------------------------------
            # STEP 10: Synchronize WABA connection state
            # -----------------------------------------------------

            locked_account = (
                WhatsAppBusinessAccount.objects
                .select_for_update()
                .select_related("company")
                .get(id=account.id)
            )

            locked_account.is_connected = True
            locked_account.is_webhook_configured = True

            locked_account.connected_at = (
                locked_account.connected_at or now
            )

            locked_account.disconnected_at = None
            locked_account.updated_by = actor

            locked_account.save(
                update_fields=[
                    "is_connected",
                    "is_webhook_configured",
                    "connected_at",
                    "disconnected_at",
                    "updated_by",
                    "updated_at",
                ]
            )

            # -----------------------------------------------------
            # STEP 11: Register selected WhatsApp number
            # -----------------------------------------------------

            logger.info(
                "[EMBEDDED SIGNUP] STEP 11 | "
                "Registering selected WhatsApp number"
            )

            registration_result = register_discovered_whatsapp_numbers(
                company=company,
                actor=actor,
                whatsapp_business_account=locked_account,
                phone_numbers=[selected_phone],
            )

            logger.info(
                "[EMBEDDED SIGNUP] NUMBER REGISTRATION COMPLETED | "
                "account_id=%s | created=%s | reused=%s",
                locked_account.id,
                registration_result["created_count"],
                registration_result["reused_count"],
            )

            # -----------------------------------------------------
            # STEP 12: Record centralized audit event
            # -----------------------------------------------------

            schedule_audit_event(
                category=AUDIT_CATEGORY_REGISTRY.META,
                action=AUDIT_ACTION_REGISTRY.CONNECT,
                description=(
                    "Cuenta de WhatsApp Business conectada "
                    "mediante Meta Embedded Signup."
                ),
                actor=actor,
                company=company,
                target=locked_account,
                metadata={
                    "source": "EMBEDDED_SIGNUP",
                    "whatsapp_business_account_id":
                        locked_account.id,
                    "meta_waba_id":
                        locked_account.meta_waba_id,
                    "meta_business_id":
                        locked_account.meta_business_id,
                    "meta_phone_number_id":
                        str(meta_phone_number_id),
                    "credential_id":
                        credential.id,
                    "is_connected":
                        locked_account.is_connected,
                    "is_webhook_configured":
                        locked_account.is_webhook_configured,
                    "created":
                        created,
                    "discovered_phone_numbers":
                        len(phone_numbers),
                    "registered_numbers":
                        len(registration_result["numbers"]),
                    "created_numbers":
                        registration_result["created_count"],
                    "reused_numbers":
                        registration_result["reused_count"],
                    "token_expiration_known":
                        token_expires_at is not None,
                },
            )

        # ---------------------------------------------------------
        # STEP 13: Prepare discovered phone numbers
        # ---------------------------------------------------------

        discovered_numbers = []

        for phone_data in phone_numbers:

            discovered_numbers.append({
                "meta_phone_number_id": str(
                    phone_data.get("id") or ""
                ),
                "display_phone_number": (
                    phone_data.get("display_phone_number") or ""
                ),
                "verified_name": (
                    phone_data.get("verified_name") or ""
                ),
                "code_verification_status": (
                    phone_data.get("code_verification_status") or ""
                ),
                "is_selected": (
                    str(phone_data.get("id"))
                    ==
                    str(meta_phone_number_id)
                ),
            })

        # ---------------------------------------------------------
        # STEP 14: Prepare registered phone numbers
        # ---------------------------------------------------------

        registered_numbers = []

        for item in registration_result["numbers"]:

            number = item["whatsapp_number"]

            registered_numbers.append({
                "id": number.id,
                "meta_phone_number_id":
                    number.meta_phone_number_id,
                "branch_id": number.branch_id,
                "created": item["created"],
            })

        # ---------------------------------------------------------
        # STEP 15: Complete onboarding
        # ---------------------------------------------------------

        logger.info(
            "[EMBEDDED SIGNUP] ONBOARDING COMPLETED | "
            "company_id=%s | "
            "account_id=%s | "
            "waba_id=%s | "
            "phone_number_id=%s | "
            "created=%s | "
            "connected=%s | "
            "webhook=%s | "
            "registered_numbers=%s",
            company.id,
            locked_account.id,
            locked_account.meta_waba_id,
            meta_phone_number_id,
            created,
            locked_account.is_connected,
            locked_account.is_webhook_configured,
            len(registered_numbers),
        )

        return {
            "whatsapp_business_account": locked_account,
            "created": created,
            "meta_waba": meta_waba,
            "meta_business_id": str(resolved_business_id),
            "selected_phone": {
                "meta_phone_number_id": str(
                    selected_phone.get("id")
                ),
                "display_phone_number": (
                    selected_phone.get("display_phone_number") or ""
                ),
                "verified_name": (
                    selected_phone.get("verified_name") or ""
                ),
                "code_verification_status": (
                    selected_phone.get("code_verification_status") or ""
                ),
            },
            "phone_numbers": discovered_numbers,
            "registered_numbers": registered_numbers,
            "created_numbers": registration_result["created_count"],
            "reused_numbers": registration_result["reused_count"],
            "is_connected": locked_account.is_connected,
            "is_webhook_configured":
                locked_account.is_webhook_configured,
            "credential_validated": True,
            "credential_expires_at": (
                token_expires_at.isoformat()
                if token_expires_at
                else None
            ),
        }

    except (
        MetaEmbeddedSignupError,
        ValidationError,
    ) as error:

        diagnostic_data = {
            "company_id": company.id,
            "actor_id": actor.id,
            "waba_id": meta_waba_id,
            "phone_number_id": meta_phone_number_id,
            "error_type": type(error).__name__,
        }

        if isinstance(error, MetaEmbeddedSignupError):
            diagnostic_data.update({
                "http_status": error.http_status,
                "meta_error_code": error.meta_error_code,
                "meta_error_subcode": error.meta_error_subcode,
            })

        logger.error(
            "[EMBEDDED SIGNUP] ONBOARDING FAILED | %s",
            diagnostic_data,
        )

        raise
