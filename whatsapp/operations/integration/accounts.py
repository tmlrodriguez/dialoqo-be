"""Business account connection and synchronization workflows."""

import logging

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.utils import timezone

from auditing.operations import schedule_audit_event
from auditing.registry import AUDIT_ACTION_REGISTRY, AUDIT_CATEGORY_REGISTRY

from whatsapp.meta.auth.credentials import get_meta_app_id
from whatsapp.meta.lifecycle import (
    MetaLifecycleAPIError,
    MetaLifecycleClient,
    log_meta_lifecycle_error,
    payload_contains_meta_id,
)
from whatsapp.models import WhatsAppBusinessAccount, WhatsAppNumber
from whatsapp.operations.credentials import prepare_waba_connection_client, store_validated_meta_credential
from whatsapp.operations.integration.numbers import validate_whatsapp_number_connection


logger = logging.getLogger(__name__)


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

    try:
        client = MetaLifecycleClient(whatsapp_business_account=account)

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

    except (ImproperlyConfigured, MetaLifecycleAPIError, ValidationError) as error:

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
        - Persist a verified global token for manually registered WABAs
          without a stored credential.
        - Subscribe Dialoqo's Meta application to the WABA.
        - Verify the resulting subscription.
        - Synchronize the account and phone-number connection states.
        - Record the successful connection through auditing.

        Notes:
        - Meta API requests execute outside database transactions.
        - Successful subscription must be verified independently.
        - Existing credentials are resolved through the WABA and never
          silently replaced with the global token.
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

    client, needs_credential = prepare_waba_connection_client(account)

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

    if needs_credential:
        store_validated_meta_credential(
            account=account,
            access_token=client.access_token,
            replace_existing=False,
        )

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

    client = MetaLifecycleClient(whatsapp_business_account=account)

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
