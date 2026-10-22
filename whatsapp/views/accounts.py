"""Business account management endpoints."""

from django.db import transaction
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_201_CREATED, HTTP_400_BAD_REQUEST
from rest_framework.views import APIView

from auditing.operations import schedule_audit_event
from auditing.registry import AUDIT_ACTION_REGISTRY, AUDIT_CATEGORY_REGISTRY
from organizations.permissions import IsOrganizationAdministrator

from whatsapp.models import WhatsAppBusinessAccount
from whatsapp.serializers.business import WhatsAppBusinessAccountSerializer
from whatsapp.serializers.snapshots import WhatsAppBusinessAccountSnapshotSerializer
from whatsapp.utils.resolvers import (
    resolve_administrative_company,
    resolve_company_whatsapp_business_account,
)


class WhatsAppBusinessAccountView(APIView):
    """
        DOCSTRING: WhatsApp Business Account View

        Description:
        - Return active WhatsApp Business Accounts belonging to a specific active company.
        - Create, partially update, and deactivate WhatsApp Business Account records.
        - Record administrative WABA mutations through the centralized audit subsystem.

        Notes:
        - The company identifier is required for every operation.
        - The company must belong to the authenticated administrative user.
        - The WABA is scoped directly to the same company.
        - Foreign WABA identifiers are treated as nonexistent.
        - Audit records are persisted only after successful database commit.
    """

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated, IsOrganizationAdministrator]
    business_serializer = WhatsAppBusinessAccountSerializer
    snapshot_serializer = WhatsAppBusinessAccountSnapshotSerializer

    def get(self, request, company_id, account_id=None):
        company = resolve_administrative_company(user=request.user, company_id=company_id)

        accounts = WhatsAppBusinessAccount.objects.select_related(
            "company",
        ).filter(
            company=company,
            is_active=True,
        )

        if account_id:
            account = resolve_company_whatsapp_business_account(company=company, account_id=account_id)

            response_payload = {
                "success_message": "Cuenta de WhatsApp Business extraída correctamente.",
                "data": self.business_serializer(account, context={"company": company}).data,
            }

            return Response(response_payload, status=HTTP_200_OK)

        response_payload = {
            "success_message": "Cuentas de WhatsApp Business extraídas correctamente.",
            "data": self.snapshot_serializer(accounts, many=True).data,
        }

        return Response(response_payload, status=HTTP_200_OK)

    def post(self, request, company_id):
        company = resolve_administrative_company(user=request.user, company_id=company_id)
        serializer = self.business_serializer(data=request.data, context={"company": company})

        if not serializer.is_valid():
            response_payload = {
                "error_message": "Creación de cuenta de WhatsApp Business rechazada: los datos proporcionados no son válidos.",
                "data": serializer.errors,
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            account = serializer.save(company=company, created_by=request.user, updated_by=request.user)

            schedule_audit_event(
                category=AUDIT_CATEGORY_REGISTRY.WHATSAPP,
                action=AUDIT_ACTION_REGISTRY.CREATE,
                description="Cuenta de WhatsApp Business creada en Dialoqo.",
                actor=request.user,
                company=company,
                target=account,
                metadata={
                    "whatsapp_business_account_id": account.id,
                    "meta_waba_id": account.meta_waba_id,
                    "meta_business_id": account.meta_business_id,
                    "is_connected": account.is_connected,
                    "is_webhook_configured": account.is_webhook_configured,
                    "is_active": account.is_active,
                },
            )

        response_payload = {
            "success_message": "Cuenta de WhatsApp Business creada correctamente.",
            "data": self.business_serializer(account, context={"company": company}).data,
        }

        return Response(response_payload, status=HTTP_201_CREATED)

    def patch(self, request, company_id, account_id):
        company = resolve_administrative_company(user=request.user, company_id=company_id)
        account = resolve_company_whatsapp_business_account(company=company, account_id=account_id)
        serializer = self.business_serializer(account, data=request.data, partial=True, context={"company": company})

        if not serializer.is_valid():
            response_payload = {
                "error_message": "Actualización de cuenta de WhatsApp Business rechazada: los datos proporcionados no son válidos.",
                "data": serializer.errors,
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        changed_fields = list(serializer.validated_data.keys())

        with transaction.atomic():
            account = serializer.save(updated_by=request.user)

            schedule_audit_event(
                category=AUDIT_CATEGORY_REGISTRY.WHATSAPP,
                action=AUDIT_ACTION_REGISTRY.UPDATE,
                description="Cuenta de WhatsApp Business actualizada en Dialoqo.",
                actor=request.user,
                company=company,
                target=account,
                metadata={
                    "whatsapp_business_account_id": account.id,
                    "meta_waba_id": account.meta_waba_id,
                    "changed_fields": changed_fields,
                    "is_connected": account.is_connected,
                    "is_webhook_configured": account.is_webhook_configured,
                    "is_active": account.is_active,
                },
            )

        response_payload = {
            "success_message": "Cuenta de WhatsApp Business actualizada correctamente.",
            "data": self.business_serializer(account, context={"company": company}).data,
        }

        return Response(response_payload, status=HTTP_200_OK)

    def delete(self, request, company_id, account_id):
        company = resolve_administrative_company(user=request.user, company_id=company_id)
        account = resolve_company_whatsapp_business_account(company=company, account_id=account_id)

        if account.numbers.filter(company=company, is_active=True).exists():
            response_payload = {
                "error_message": "Desactivación de cuenta rechazada: existen números de WhatsApp activos asociados.",
                "data": {},
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            account.is_active = False
            account.updated_by = request.user
            account.save(update_fields=["is_active", "updated_by", "updated_at"])

            schedule_audit_event(
                category=AUDIT_CATEGORY_REGISTRY.WHATSAPP,
                action=AUDIT_ACTION_REGISTRY.DEACTIVATE,
                description="Cuenta de WhatsApp Business desactivada en Dialoqo.",
                actor=request.user,
                company=company,
                target=account,
                metadata={
                    "whatsapp_business_account_id": account.id,
                    "meta_waba_id": account.meta_waba_id,
                    "is_connected": account.is_connected,
                    "is_webhook_configured": account.is_webhook_configured,
                    "is_active": account.is_active,
                },
            )

        response_payload = {
            "success_message": "Cuenta de WhatsApp Business desactivada correctamente.",
            "data": self.business_serializer(account, context={"company": company}).data,
        }

        return Response(response_payload, status=HTTP_200_OK)
