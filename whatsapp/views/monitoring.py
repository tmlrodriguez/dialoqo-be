"""Monitoring lifecycle and available monitoring context endpoints."""

from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_400_BAD_REQUEST
from rest_framework.views import APIView

from access.registry import ROLE_REGISTRY
from organizations.models import Branch, Company, UserCompanyAccess
from organizations.permissions import IsOrganizationAdministrator

from whatsapp.models import NumberAssignment, WhatsAppNumber
from whatsapp.operations.monitoring import (
    activate_whatsapp_monitoring,
    deactivate_whatsapp_monitoring,
)
from whatsapp.serializers.monitoring import MonitoringCompanySerializer
from whatsapp.serializers.snapshots import WhatsAppNumberSnapshotSerializer
from whatsapp.utils.permissions import IsMonitoringUser
from whatsapp.utils.resolvers import (
    resolve_administrative_company,
    resolve_company_branch,
    resolve_company_whatsapp_number,
)


class WhatsAppMonitoringView(APIView):
    """
        DOCSTRING: WhatsApp Monitoring View

        Description:
        - Activate and deactivate monitored message capture for a corporate WhatsApp number.

        Notes:
        - The number is resolved through the full administrative company and branch tenant hierarchy.
        - Foreign number identifiers return not found.
        - Monitoring lifecycle auditing is implemented inside the operation layer.
    """

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated, IsOrganizationAdministrator]
    snapshot_serializer = WhatsAppNumberSnapshotSerializer

    def post(self, request, company_id, branch_id, number_id):
        company = resolve_administrative_company(user=request.user, company_id=company_id)
        branch = resolve_company_branch(company=company, branch_id=branch_id)
        whatsapp_number = resolve_company_whatsapp_number(company=company, branch_id=branch.id, number_id=number_id)

        try:
            whatsapp_number = activate_whatsapp_monitoring(whatsapp_number=whatsapp_number, actor=request.user)

        except ValidationError as error:
            response_payload = {
                "error_message": "Activación de monitoreo rechazada: no fue posible completar la operación.",
                "data": error.message_dict,
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        response_payload = {
            "success_message": "Monitoreo de WhatsApp activado correctamente.",
            "data": self.snapshot_serializer(whatsapp_number).data,
        }

        return Response(response_payload, status=HTTP_200_OK)

    def delete(self, request, company_id, branch_id, number_id):
        company = resolve_administrative_company(user=request.user, company_id=company_id)
        branch = resolve_company_branch(company=company, branch_id=branch_id)
        whatsapp_number = resolve_company_whatsapp_number(company=company, branch_id=branch.id, number_id=number_id)

        try:
            whatsapp_number = deactivate_whatsapp_monitoring(whatsapp_number=whatsapp_number, actor=request.user)

        except ValidationError as error:
            response_payload = {
                "error_message": "Desactivación de monitoreo rechazada: no fue posible completar la operación.",
                "data": error.message_dict,
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        response_payload = {
            "success_message": "Monitoreo de WhatsApp desactivado correctamente.",
            "data": self.snapshot_serializer(whatsapp_number).data,
        }

        return Response(response_payload, status=HTTP_200_OK)


class MonitoringContextView(APIView):
    """
        DOCSTRING: Monitoring Context View

        Description:
        - Return the complete monitoring context available to the authenticated MONITOR or MEMBER user.
        - Provide authorized companies, active branches, and operational WhatsApp numbers.
        - Supply the hierarchical context required by the Dialoqo monitoring frontend.

        Notes:
        - MONITOR users receive operational numbers through active company-access assignments.
        - MEMBER users receive only operational numbers currently assigned to them.
        - Only active companies, branches, WABA accounts, connected numbers, and monitoring-enabled numbers are returned.
        - This operation is read-only and does not generate audit events.
    """

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated, IsMonitoringUser]
    serializer_class = MonitoringCompanySerializer

    def get(self, request):
        if request.user.role == ROLE_REGISTRY.MONITOR:
            authorized_company_ids = UserCompanyAccess.objects.filter(
                user=request.user,
                is_active=True,
                company__is_active=True,
            ).values_list("company_id", flat=True)

            monitoring_numbers = WhatsAppNumber.objects.select_related(
                "company",
                "branch",
                "whatsapp_business_account",
            ).filter(
                company_id__in=authorized_company_ids,
                company__is_active=True,
                branch__is_active=True,
                whatsapp_business_account__is_active=True,
                is_active=True,
                is_connected=True,
                is_monitoring_enabled=True,
            ).order_by("display_name", "id")

        else:
            assigned_number_ids = NumberAssignment.objects.filter(
                member=request.user,
                member__is_active=True,
                is_active=True,
                unassigned_at__isnull=True,
                whatsapp_number__company__is_active=True,
                whatsapp_number__branch__is_active=True,
                whatsapp_number__whatsapp_business_account__is_active=True,
                whatsapp_number__is_active=True,
                whatsapp_number__is_connected=True,
                whatsapp_number__is_monitoring_enabled=True,
            ).values_list("whatsapp_number_id", flat=True)

            monitoring_numbers = WhatsAppNumber.objects.select_related(
                "company",
                "branch",
                "whatsapp_business_account",
            ).filter(
                id__in=assigned_number_ids,
                company__is_active=True,
                branch__is_active=True,
                whatsapp_business_account__is_active=True,
                is_active=True,
                is_connected=True,
                is_monitoring_enabled=True,
            ).order_by("display_name", "id")

            authorized_company_ids = monitoring_numbers.values_list("company_id", flat=True)

        monitoring_branches = Branch.objects.filter(
            company_id__in=authorized_company_ids,
            company__is_active=True,
            is_active=True,
            whatsapp_numbers__in=monitoring_numbers,
        ).distinct().order_by("name", "id").prefetch_related(
            Prefetch("whatsapp_numbers", queryset=monitoring_numbers, to_attr="monitoring_numbers")
        )

        companies = Company.objects.filter(
            id__in=authorized_company_ids,
            is_active=True,
            branches__in=monitoring_branches,
        ).distinct().order_by("name", "id").prefetch_related(
            Prefetch("branches", queryset=monitoring_branches, to_attr="monitoring_branches")
        )

        response_payload = {
            "success_message": "Contexto de monitoreo extraído correctamente.",
            "data": self.serializer_class(companies, many=True).data,
        }

        return Response(response_payload, status=HTTP_200_OK)
