"""WhatsApp number management endpoints."""

from django.db import transaction
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_201_CREATED, HTTP_400_BAD_REQUEST
from rest_framework.views import APIView

from auditing.operations import schedule_audit_event
from auditing.registry import AUDIT_ACTION_REGISTRY, AUDIT_CATEGORY_REGISTRY
from organizations.permissions import IsOrganizationAdministrator

from whatsapp.models import WhatsAppNumber
from whatsapp.serializers.business import WhatsAppNumberSerializer
from whatsapp.serializers.snapshots import WhatsAppNumberSnapshotSerializer
from whatsapp.utils.resolvers import (
    resolve_administrative_company,
    resolve_company_branch,
    resolve_company_whatsapp_number,
)


class WhatsAppNumberView(APIView):
    """
        DOCSTRING: WhatsApp Number View

        Description:
        - Return active WhatsApp numbers belonging to a specific company and branch.
        - Create, partially update, and deactivate corporate WhatsApp numbers.
        - Record administrative number mutations through the centralized audit subsystem.

        Notes:
        - Company and branch identifiers are tenant-scoped.
        - The associated WABA must remain inside the same company.
        - Foreign number identifiers are treated as nonexistent.
        - Audit metadata intentionally avoids unnecessary conversation content.
    """

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated, IsOrganizationAdministrator]
    business_serializer = WhatsAppNumberSerializer
    snapshot_serializer = WhatsAppNumberSnapshotSerializer

    def get(self, request, company_id, branch_id, number_id=None):
        company = resolve_administrative_company(user=request.user, company_id=company_id)
        branch = resolve_company_branch(company=company, branch_id=branch_id)

        numbers = WhatsAppNumber.objects.select_related(
            "company",
            "branch",
            "whatsapp_business_account",
            "whatsapp_business_account__company",
        ).filter(
            company=company,
            branch=branch,
            branch__company=company,
            whatsapp_business_account__company=company,
            is_active=True,
        )

        if number_id:
            whatsapp_number = resolve_company_whatsapp_number(company=company, branch_id=branch.id, number_id=number_id)

            response_payload = {
                "success_message": "Número de WhatsApp extraído correctamente.",
                "data": self.business_serializer(whatsapp_number, context={"company": company}).data,
            }

            return Response(response_payload, status=HTTP_200_OK)

        response_payload = {
            "success_message": "Números de WhatsApp extraídos correctamente.",
            "data": self.snapshot_serializer(numbers, many=True).data,
        }

        return Response(response_payload, status=HTTP_200_OK)

    def post(self, request, company_id, branch_id):
        company = resolve_administrative_company(user=request.user, company_id=company_id)
        branch = resolve_company_branch(company=company, branch_id=branch_id)
        serializer = self.business_serializer(data=request.data, context={"company": company})

        if not serializer.is_valid():
            response_payload = {
                "error_message": "Creación de número de WhatsApp rechazada: los datos proporcionados no son válidos.",
                "data": serializer.errors,
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            whatsapp_number = serializer.save(company=company, branch=branch, created_by=request.user, updated_by=request.user)

            schedule_audit_event(
                category=AUDIT_CATEGORY_REGISTRY.WHATSAPP,
                action=AUDIT_ACTION_REGISTRY.CREATE,
                description="Número de WhatsApp creado en Dialoqo.",
                actor=request.user,
                company=company,
                branch=branch,
                target=whatsapp_number,
                metadata={
                    "whatsapp_number_id": whatsapp_number.id,
                    "whatsapp_business_account_id": whatsapp_number.whatsapp_business_account_id,
                    "meta_phone_number_id": whatsapp_number.meta_phone_number_id,
                    "is_connected": whatsapp_number.is_connected,
                    "is_monitoring_enabled": whatsapp_number.is_monitoring_enabled,
                    "is_active": whatsapp_number.is_active,
                },
            )

        response_payload = {
            "success_message": "Número de WhatsApp creado correctamente.",
            "data": self.business_serializer(whatsapp_number, context={"company": company}).data,
        }

        return Response(response_payload, status=HTTP_201_CREATED)

    def patch(self, request, company_id, branch_id, number_id):
        company = resolve_administrative_company(user=request.user, company_id=company_id)
        branch = resolve_company_branch(company=company, branch_id=branch_id)
        whatsapp_number = resolve_company_whatsapp_number(company=company, branch_id=branch.id, number_id=number_id)
        serializer = self.business_serializer(whatsapp_number, data=request.data, partial=True, context={"company": company})

        if not serializer.is_valid():
            response_payload = {
                "error_message": "Actualización de número de WhatsApp rechazada: los datos proporcionados no son válidos.",
                "data": serializer.errors,
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        changed_fields = list(serializer.validated_data.keys())
        previous_waba_id = whatsapp_number.whatsapp_business_account_id

        with transaction.atomic():
            whatsapp_number = serializer.save(company=company, branch=branch, updated_by=request.user)

            schedule_audit_event(
                category=AUDIT_CATEGORY_REGISTRY.WHATSAPP,
                action=AUDIT_ACTION_REGISTRY.UPDATE,
                description="Número de WhatsApp actualizado en Dialoqo.",
                actor=request.user,
                company=company,
                branch=branch,
                target=whatsapp_number,
                metadata={
                    "whatsapp_number_id": whatsapp_number.id,
                    "previous_whatsapp_business_account_id": previous_waba_id,
                    "current_whatsapp_business_account_id": whatsapp_number.whatsapp_business_account_id,
                    "meta_phone_number_id": whatsapp_number.meta_phone_number_id,
                    "changed_fields": changed_fields,
                    "is_connected": whatsapp_number.is_connected,
                    "is_monitoring_enabled": whatsapp_number.is_monitoring_enabled,
                    "is_active": whatsapp_number.is_active,
                },
            )

        response_payload = {
            "success_message": "Número de WhatsApp actualizado correctamente.",
            "data": self.business_serializer(whatsapp_number, context={"company": company}).data,
        }

        return Response(response_payload, status=HTTP_200_OK)

    def delete(self, request, company_id, branch_id, number_id):
        company = resolve_administrative_company(user=request.user, company_id=company_id)
        branch = resolve_company_branch(company=company, branch_id=branch_id)
        whatsapp_number = resolve_company_whatsapp_number(company=company, branch_id=branch.id, number_id=number_id)

        if whatsapp_number.is_monitoring_enabled:
            response_payload = {
                "error_message": "Desactivación de número rechazada: el monitoreo se encuentra activo.",
                "data": {},
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            whatsapp_number.is_active = False
            whatsapp_number.updated_by = request.user
            whatsapp_number.save(update_fields=["is_active", "updated_by", "updated_at"])

            schedule_audit_event(
                category=AUDIT_CATEGORY_REGISTRY.WHATSAPP,
                action=AUDIT_ACTION_REGISTRY.DEACTIVATE,
                description="Número de WhatsApp desactivado en Dialoqo.",
                actor=request.user,
                company=company,
                branch=branch,
                target=whatsapp_number,
                metadata={
                    "whatsapp_number_id": whatsapp_number.id,
                    "whatsapp_business_account_id": whatsapp_number.whatsapp_business_account_id,
                    "meta_phone_number_id": whatsapp_number.meta_phone_number_id,
                    "is_connected": whatsapp_number.is_connected,
                    "is_monitoring_enabled": whatsapp_number.is_monitoring_enabled,
                    "is_active": whatsapp_number.is_active,
                },
            )

        response_payload = {
            "success_message": "Número de WhatsApp desactivado correctamente.",
            "data": self.business_serializer(whatsapp_number, context={"company": company}).data,
        }

        return Response(response_payload, status=HTTP_200_OK)
