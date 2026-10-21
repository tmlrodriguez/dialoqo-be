import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from auditing.operations import schedule_audit_event
from auditing.registry import (
    AUDIT_ACTION_REGISTRY,
    AUDIT_CATEGORY_REGISTRY,
)

from .credentials import get_meta_app_id
from .meta_lifecycle import (
    MetaLifecycleAPIError,
    MetaLifecycleClient,
    payload_contains_meta_id,
)
from .models import WhatsAppBusinessAccount, WhatsAppNumber
from .webhook import normalize_phone_number


logger = logging.getLogger(__name__)


# Define your Meta lifecycle operations here.


def log_meta_lifecycle_error(operation, error, **context):
    """
        DOCSTRING: Log Meta Lifecycle Error

        Description:
        - Generate structured diagnostic logs for Meta lifecycle failures.
        - Preserve relevant resource identifiers and error classifications.

        Notes:
        - Access tokens and application secrets are never logged.
        - Raw Meta responses are never logged.
        - Customer phone numbers and message contents are excluded.
        - Meta error codes are retained for troubleshooting.
    """

    diagnostic_data = {
        "operation": operation,
        "error_type": type(error).__name__,
        **context,
    }

    if isinstance(error, MetaLifecycleAPIError):
        diagnostic_data.update({
            "http_status": error.http_status,
            "meta_error_code": error.meta_error_code,
            "meta_error_subcode": error.meta_error_subcode,
        })

    logger.error(
        "[META LIFECYCLE] ERROR | %s",
        diagnostic_data,
    )


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
        - Synchronize the local connection state.
        - Generate structured diagnostic logs.

        Notes:
        - Meta API calls execute outside database transactions.
        - Database locks are acquired only while saving state.
        - Failed validation persists the disconnected state.
        - Internal validations may disable audit generation.
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

        if client is None:
            client = MetaLifecycleClient()

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

    except (MetaLifecycleAPIError, ValidationError) as error:

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


def refresh_whatsapp_business_account_state(
    whatsapp_business_account,
    actor,
    audit_event=True,
):
    """
        DOCSTRING: Refresh WhatsApp Business Account State

        Description:
        - Read the current Meta WABA state.
        - Verify application webhook subscription.
        - Synchronize WABA and registered phone-number connection states.
        - Generate structured lifecycle diagnostics.

        Notes:
        - Meta API requests execute outside database transactions.
        - Failed validation updates remain committed.
        - Phone-number validation occurs independently.
        - Internal refresh operations may disable audit generation.
    """

    account = WhatsAppBusinessAccount.objects.select_related(
        "company"
    ).get(
        id=whatsapp_business_account.id
    )

    logger.info(
        "[META WABA] REFRESH START | "
        "company_id=%s | account_id=%s | waba_id=%s",
        account.company_id,
        account.id,
        account.meta_waba_id,
    )

    client = MetaLifecycleClient()

    try:
        logger.info(
            "[META WABA] VALIDATING CREDENTIALS | account_id=%s",
            account.id,
        )

        client.validate_credentials()

        logger.info(
            "[META WABA] FETCHING ACCOUNT | waba_id=%s",
            account.meta_waba_id,
        )

        meta_waba = client.get_waba(
            account.meta_waba_id
        )

        if str(meta_waba.get("id")) != str(account.meta_waba_id):
            raise ValidationError({
                "meta_waba_id":
                    "Validación de cuenta rechazada: "
                    "Meta devolvió una WABA diferente."
            })

        logger.info(
            "[META WABA] ACCOUNT VERIFIED | waba_id=%s",
            account.meta_waba_id,
        )

        subscribed_apps = client.list_subscribed_apps(
            account.meta_waba_id
        )

        webhook_configured = payload_contains_meta_id(
            subscribed_apps,
            get_meta_app_id(),
        )

        logger.info(
            "[META WABA] WEBHOOK SUBSCRIPTION CHECK | "
            "waba_id=%s | subscribed=%s",
            account.meta_waba_id,
            webhook_configured,
        )

    except (MetaLifecycleAPIError, ValidationError) as error:

        log_meta_lifecycle_error(
            operation="refresh_whatsapp_business_account_state",
            error=error,
            company_id=account.company_id,
            account_id=account.id,
            waba_id=account.meta_waba_id,
        )

        now = timezone.now()

        with transaction.atomic():

            locked_account = WhatsAppBusinessAccount.objects.select_for_update().get(
                id=account.id
            )

            locked_account.is_connected = False
            locked_account.is_webhook_configured = False
            locked_account.disconnected_at = now
            locked_account.updated_by = actor

            locked_account.save(
                update_fields=[
                    "is_connected",
                    "is_webhook_configured",
                    "disconnected_at",
                    "updated_by",
                    "updated_at",
                ]
            )

            locked_account.numbers.filter(
                is_active=True
            ).update(
                is_connected=False,
                updated_at=now,
            )

        logger.warning(
            "[META WABA] REFRESH FAILED | "
            "account_id=%s | connected=False",
            account.id,
        )

        raise

    now = timezone.now()

    with transaction.atomic():

        locked_account = WhatsAppBusinessAccount.objects.select_for_update().select_related(
            "company"
        ).get(
            id=account.id
        )

        locked_account.is_connected = True
        locked_account.is_webhook_configured = webhook_configured
        locked_account.connected_at = locked_account.connected_at or now
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

    number_results = []

    numbers = list(
        WhatsAppNumber.objects.filter(
            whatsapp_business_account_id=account.id,
            is_active=True,
        ).order_by("id")
    )

    logger.info(
        "[META WABA] VALIDATING REGISTERED NUMBERS | "
        "account_id=%s | count=%s",
        account.id,
        len(numbers),
    )

    for number in numbers:

        try:
            result = validate_whatsapp_number_connection(
                whatsapp_number=number,
                actor=actor,
                client=client,
                audit_event=False,
            )

            number_results.append({
                "id": number.id,
                "connected": True,
                "meta_phone": result["meta_phone"],
            })

        except (ValidationError, MetaLifecycleAPIError) as error:

            log_meta_lifecycle_error(
                operation="refresh_waba_number_validation",
                error=error,
                account_id=account.id,
                number_id=number.id,
            )

            number_results.append({
                "id": number.id,
                "connected": False,
                "error": str(error),
            })

    connected_count = sum(
        1 for result in number_results
        if result["connected"]
    )

    disconnected_count = len(number_results) - connected_count

    if audit_event:

        with transaction.atomic():

            schedule_audit_event(
                category=AUDIT_CATEGORY_REGISTRY.META,
                action=AUDIT_ACTION_REGISTRY.SYNCHRONIZE,
                description="Estado de cuenta de WhatsApp Business sincronizado con Meta.",
                actor=actor,
                company=locked_account.company,
                target=locked_account,
                metadata={
                    "whatsapp_business_account_id": locked_account.id,
                    "meta_waba_id": locked_account.meta_waba_id,
                    "is_connected": locked_account.is_connected,
                    "is_webhook_configured":
                        locked_account.is_webhook_configured,
                    "validated_numbers": len(number_results),
                    "connected_numbers": connected_count,
                    "disconnected_numbers": disconnected_count,
                },
            )

    logger.info(
        "[META WABA] REFRESH COMPLETED | "
        "account_id=%s | connected=%s | webhook=%s | "
        "numbers=%s | connected_numbers=%s | disconnected_numbers=%s",
        locked_account.id,
        locked_account.is_connected,
        locked_account.is_webhook_configured,
        len(number_results),
        connected_count,
        disconnected_count,
    )

    return {
        "whatsapp_business_account": locked_account,
        "meta_waba": meta_waba,
        "subscribed_apps": subscribed_apps,
        "numbers": number_results,
    }


def connect_whatsapp_business_account(whatsapp_business_account, actor):
    """
        DOCSTRING: Connect WhatsApp Business Account

        Description:
        - Validate Meta credentials and WABA accessibility.
        - Subscribe Dialoqo's Meta application to the WABA.
        - Verify the resulting subscription.
        - Synchronize the account and phone-number connection states.
        - Record the successful connection through auditing.

        Notes:
        - Meta API requests execute outside database transactions.
        - Successful subscription must be verified independently.
        - Internal refresh does not create duplicate audit events.
        - Sensitive Meta credentials are never logged.
    """

    account = WhatsAppBusinessAccount.objects.select_related(
        "company"
    ).get(
        id=whatsapp_business_account.id
    )

    logger.info(
        "[META WABA] CONNECTION START | "
        "company_id=%s | account_id=%s | waba_id=%s",
        account.company_id,
        account.id,
        account.meta_waba_id,
    )

    client = MetaLifecycleClient()

    try:

        logger.info(
            "[META WABA] VALIDATING CREDENTIALS | account_id=%s",
            account.id,
        )

        client.validate_credentials()

        meta_waba = client.get_waba(
            account.meta_waba_id
        )

        if str(meta_waba.get("id")) != str(account.meta_waba_id):
            raise ValidationError({
                "meta_waba_id":
                    "Conexión de cuenta rechazada: "
                    "Meta devolvió una WABA diferente."
            })

        logger.info(
            "[META WABA] ACCESS CONFIRMED | waba_id=%s",
            account.meta_waba_id,
        )

        logger.info(
            "[META WABA] SUBSCRIBING APPLICATION | "
            "waba_id=%s | app_id=%s",
            account.meta_waba_id,
            get_meta_app_id(),
        )

        client.subscribe_waba(
            account.meta_waba_id
        )

        subscribed_apps = client.list_subscribed_apps(
            account.meta_waba_id
        )

        subscription_confirmed = payload_contains_meta_id(
            subscribed_apps,
            get_meta_app_id(),
        )

        if not subscription_confirmed:
            raise ValidationError({
                "webhook":
                    "Conexión de cuenta rechazada: "
                    "Meta no confirmó la suscripción de la aplicación."
            })

        logger.info(
            "[META WABA] SUBSCRIPTION CONFIRMED | "
            "waba_id=%s",
            account.meta_waba_id,
        )

    except (MetaLifecycleAPIError, ValidationError) as error:

        log_meta_lifecycle_error(
            operation="connect_whatsapp_business_account",
            error=error,
            company_id=account.company_id,
            account_id=account.id,
            waba_id=account.meta_waba_id,
        )

        raise

    result = refresh_whatsapp_business_account_state(
        whatsapp_business_account=account,
        actor=actor,
        audit_event=False,
    )

    synchronized_account = result["whatsapp_business_account"]

    connected_count = sum(
        1 for number in result["numbers"]
        if number["connected"]
    )

    disconnected_count = (
        len(result["numbers"]) - connected_count
    )

    with transaction.atomic():

        schedule_audit_event(
            category=AUDIT_CATEGORY_REGISTRY.META,
            action=AUDIT_ACTION_REGISTRY.CONNECT,
            description="Cuenta de WhatsApp Business conectada correctamente con Meta.",
            actor=actor,
            company=synchronized_account.company,
            target=synchronized_account,
            metadata={
                "whatsapp_business_account_id":
                    synchronized_account.id,
                "meta_app_id": get_meta_app_id(),
                "meta_waba_id":
                    synchronized_account.meta_waba_id,
                "is_connected":
                    synchronized_account.is_connected,
                "is_webhook_configured":
                    synchronized_account.is_webhook_configured,
                "validated_numbers": len(result["numbers"]),
                "connected_numbers": connected_count,
                "disconnected_numbers": disconnected_count,
            },
        )

    logger.info(
        "[META WABA] CONNECTION COMPLETED | "
        "company_id=%s | "
        "account_id=%s | "
        "waba_id=%s | "
        "connected=%s | "
        "webhook=%s | "
        "validated_numbers=%s | "
        "connected_numbers=%s | "
        "disconnected_numbers=%s",
        synchronized_account.company_id,
        synchronized_account.id,
        synchronized_account.meta_waba_id,
        synchronized_account.is_connected,
        synchronized_account.is_webhook_configured,
        len(result["numbers"]),
        connected_count,
        disconnected_count,
    )

    return result


def disconnect_whatsapp_business_account(
    whatsapp_business_account,
    actor,
):
    """
        DOCSTRING: Disconnect WhatsApp Business Account

        Description:
        - Remove Dialoqo's Meta application subscription from
          the configured WhatsApp Business Account.
        - Verify that Meta no longer reports the application
          as subscribed.
        - Synchronize local WABA and phone-number connection states.
        - Preserve historical WhatsApp conversations and messages.
        - Record the successful disconnection through auditing.
        - Generate structured lifecycle diagnostic logs.

        Notes:
        - Meta API requests execute outside database transactions.
        - Active monitoring prevents WABA disconnection.
        - External subscription removal must be verified.
        - Database modifications are performed atomically.
        - Existing WhatsApp messages are never deleted.
        - Sensitive Meta credentials are never logged.
        - Customer phone numbers and message contents are excluded.
        - Audit events are scheduled only after successful
          local database operations.
    """

    account = WhatsAppBusinessAccount.objects.select_related(
        "company"
    ).get(
        id=whatsapp_business_account.id
    )

    logger.info(
        "[META WABA] DISCONNECTION START | "
        "company_id=%s | "
        "account_id=%s | "
        "waba_id=%s",
        account.company_id,
        account.id,
        account.meta_waba_id,
    )

    # ---------------------------------------------------------
    # STEP 1: Validate monitoring state
    # ---------------------------------------------------------

    monitoring_numbers_count = account.numbers.filter(
        is_active=True,
        is_monitoring_enabled=True,
    ).count()

    logger.info(
        "[META WABA] MONITORING CHECK | "
        "account_id=%s | "
        "active_monitoring_numbers=%s",
        account.id,
        monitoring_numbers_count,
    )

    if monitoring_numbers_count > 0:

        logger.warning(
            "[META WABA] DISCONNECTION BLOCKED | "
            "account_id=%s | "
            "reason=ACTIVE_MONITORING | "
            "monitoring_numbers=%s",
            account.id,
            monitoring_numbers_count,
        )

        raise ValidationError({
            "whatsapp_business_account":
                "Desconexión de cuenta rechazada: "
                "existen números con monitoreo activo."
        })

    # ---------------------------------------------------------
    # STEP 2: Initialize Meta lifecycle client
    # ---------------------------------------------------------

    client = MetaLifecycleClient()

    try:

        # -----------------------------------------------------
        # STEP 3: Validate Meta credentials
        # -----------------------------------------------------

        logger.info(
            "[META WABA] VALIDATING CREDENTIALS | "
            "account_id=%s",
            account.id,
        )

        client.validate_credentials()

        logger.info(
            "[META WABA] CREDENTIALS VALIDATED | "
            "account_id=%s",
            account.id,
        )

        # -----------------------------------------------------
        # STEP 4: Remove Meta application subscription
        # -----------------------------------------------------

        logger.info(
            "[META WABA] UNSUBSCRIBING APPLICATION | "
            "waba_id=%s | "
            "app_id=%s",
            account.meta_waba_id,
            get_meta_app_id(),
        )

        client.unsubscribe_waba(
            account.meta_waba_id
        )

        logger.info(
            "[META WABA] UNSUBSCRIBE REQUEST COMPLETED | "
            "waba_id=%s",
            account.meta_waba_id,
        )

        # -----------------------------------------------------
        # STEP 5: Verify actual subscription state
        # -----------------------------------------------------

        logger.info(
            "[META WABA] VERIFYING SUBSCRIPTION REMOVAL | "
            "waba_id=%s",
            account.meta_waba_id,
        )

        subscribed_apps = client.list_subscribed_apps(
            account.meta_waba_id
        )

        subscription_still_exists = payload_contains_meta_id(
            subscribed_apps,
            get_meta_app_id(),
        )

        logger.info(
            "[META WABA] SUBSCRIPTION REMOVAL CHECK | "
            "waba_id=%s | "
            "subscription_exists=%s",
            account.meta_waba_id,
            subscription_still_exists,
        )

        if subscription_still_exists:

            raise ValidationError({
                "webhook":
                    "Desconexión de cuenta rechazada: "
                    "Meta todavía reporta la aplicación "
                    "como suscrita a la WABA."
            })

        logger.info(
            "[META WABA] SUBSCRIPTION REMOVAL CONFIRMED | "
            "waba_id=%s",
            account.meta_waba_id,
        )

    except (MetaLifecycleAPIError, ValidationError) as error:

        log_meta_lifecycle_error(
            operation="disconnect_whatsapp_business_account",
            error=error,
            company_id=account.company_id,
            account_id=account.id,
            waba_id=account.meta_waba_id,
        )

        logger.warning(
            "[META WABA] DISCONNECTION FAILED | "
            "account_id=%s | "
            "error_type=%s",
            account.id,
            type(error).__name__,
        )

        raise

    # ---------------------------------------------------------
    # STEP 6: Persist local disconnection state
    # ---------------------------------------------------------

    now = timezone.now()

    with transaction.atomic():

        locked_account = (
            WhatsAppBusinessAccount.objects
            .select_for_update()
            .select_related("company")
            .get(id=account.id)
        )

        # Revalidate monitoring under the database lock.
        #
        # This protects against a monitoring activation that
        # committed before this transaction acquired the lock.

        if locked_account.numbers.filter(
            is_active=True,
            is_monitoring_enabled=True,
        ).exists():

            logger.warning(
                "[META WABA] LOCAL DISCONNECTION BLOCKED | "
                "account_id=%s | "
                "reason=MONITORING_ACTIVATED_DURING_DISCONNECTION",
                locked_account.id,
            )

            raise ValidationError({
                "whatsapp_business_account":
                    "Desconexión local rechazada: "
                    "se detectaron números con monitoreo activo. "
                    "Verifique el estado de la suscripción en Meta."
            })

        active_number_ids = list(
            locked_account.numbers.filter(
                is_active=True
            ).values_list(
                "id",
                flat=True,
            )
        )

        locked_account.is_connected = False
        locked_account.is_webhook_configured = False
        locked_account.disconnected_at = now
        locked_account.updated_by = actor

        locked_account.save(
            update_fields=[
                "is_connected",
                "is_webhook_configured",
                "disconnected_at",
                "updated_by",
                "updated_at",
            ]
        )

        disconnected_numbers_count = (
            locked_account.numbers.filter(
                is_active=True
            ).update(
                is_connected=False,
                updated_at=now,
            )
        )

        # -----------------------------------------------------
        # STEP 7: Schedule centralized audit event
        # -----------------------------------------------------

        schedule_audit_event(
            category=AUDIT_CATEGORY_REGISTRY.META,
            action=AUDIT_ACTION_REGISTRY.DISCONNECT,
            description="Cuenta de WhatsApp Business desconectada de Meta.",
            actor=actor,
            company=locked_account.company,
            target=locked_account,
            metadata={
                "whatsapp_business_account_id":
                    locked_account.id,
                "meta_app_id": get_meta_app_id(),
                "meta_waba_id":
                    locked_account.meta_waba_id,
                "is_connected":
                    locked_account.is_connected,
                "is_webhook_configured":
                    locked_account.is_webhook_configured,
                "disconnected_at":
                    locked_account.disconnected_at.isoformat(),
                "disconnected_number_ids":
                    active_number_ids,
            },
        )

    # ---------------------------------------------------------
    # STEP 8: Log successful disconnection
    # ---------------------------------------------------------

    logger.info(
        "[META WABA] LOCAL STATE UPDATED | "
        "account_id=%s | "
        "connected=False | "
        "webhook=False | "
        "disconnected_numbers=%s",
        locked_account.id,
        disconnected_numbers_count,
    )

    logger.info(
        "[META WABA] DISCONNECTION COMPLETED | "
        "company_id=%s | "
        "account_id=%s | "
        "waba_id=%s | "
        "disconnected_at=%s",
        locked_account.company_id,
        locked_account.id,
        locked_account.meta_waba_id,
        locked_account.disconnected_at.isoformat(),
    )

    return {
        "whatsapp_business_account": locked_account,
        "subscribed_apps": subscribed_apps,
    }