"""WhatsApp number assignment endpoints."""

from django.core.exceptions import ValidationError
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_201_CREATED, HTTP_400_BAD_REQUEST
from rest_framework.views import APIView

from organizations.permissions import IsOrganizationAdministrator

from whatsapp.models import NumberAssignment
from whatsapp.operations.assignments import (
    assign_whatsapp_number,
    get_current_whatsapp_number_assignment,
    unassign_whatsapp_number,
)
from whatsapp.serializers.business import NumberAssignmentSerializer
from whatsapp.serializers.snapshots import NumberAssignmentSnapshotSerializer
from whatsapp.utils.resolvers import (
    resolve_administrative_company,
    resolve_company_branch,
    resolve_company_whatsapp_number,
    resolve_whatsapp_number_assignment,
)


class NumberAssignmentView(APIView):
    """
        DOCSTRING: Number Assignment View

        Description:
        - Return current and historical assignments associated with a corporate WhatsApp number.
        - Assign an unassigned number to an eligible Dialoqo MEMBER user without another active assignment.
        - End the currently active assignment.

        Notes:
        - Company, branch, and WhatsApp number relationships remain tenant-scoped.
        - Eligible members are AccessUser records with role MEMBER owned by the same administrator.
        - Historical assignments remain preserved.
        - Reassignment requires explicitly ending the existing assignment first.
        - Assignment lifecycle auditing is implemented inside the operation layer.
    """

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated, IsOrganizationAdministrator]
    business_serializer = NumberAssignmentSerializer
    snapshot_serializer = NumberAssignmentSnapshotSerializer

    def get(self, request, company_id, branch_id, number_id, assignment_id=None):
        company = resolve_administrative_company(user=request.user, company_id=company_id)
        branch = resolve_company_branch(company=company, branch_id=branch_id)
        whatsapp_number = resolve_company_whatsapp_number(company=company, branch_id=branch.id, number_id=number_id)

        assignments = NumberAssignment.objects.select_related("member").filter(
            whatsapp_number=whatsapp_number,
            member__created_by=request.user,
        ).order_by("-assigned_at", "-id")

        if assignment_id:
            assignment = resolve_whatsapp_number_assignment(whatsapp_number=whatsapp_number, assignment_id=assignment_id)

            response_payload = {
                "success_message": "Asignación de número extraída correctamente.",
                "data": self.snapshot_serializer(assignment).data,
            }

            return Response(response_payload, status=HTTP_200_OK)

        current_assignment = get_current_whatsapp_number_assignment(whatsapp_number=whatsapp_number)

        response_payload = {
            "success_message": "Historial de asignaciones extraído correctamente.",
            "data": {
                "current_assignment": self.snapshot_serializer(current_assignment).data if current_assignment else None,
                "history": self.snapshot_serializer(assignments, many=True).data,
            },
        }

        return Response(response_payload, status=HTTP_200_OK)

    def post(self, request, company_id, branch_id, number_id):
        company = resolve_administrative_company(user=request.user, company_id=company_id)
        branch = resolve_company_branch(company=company, branch_id=branch_id)
        whatsapp_number = resolve_company_whatsapp_number(company=company, branch_id=branch.id, number_id=number_id)
        serializer = self.business_serializer(data=request.data, context={"whatsapp_number": whatsapp_number})

        if not serializer.is_valid():
            response_payload = {
                "error_message": "Asignación de número rechazada: los datos proporcionados no son válidos.",
                "data": serializer.errors,
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        try:
            assignment = assign_whatsapp_number(
                whatsapp_number=whatsapp_number,
                member=serializer.validated_data["member"],
                actor=request.user,
            )

        except ValidationError as error:
            response_payload = {
                "error_message": "Asignación de número rechazada: no fue posible completar la operación.",
                "data": error.message_dict,
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        response_payload = {
            "success_message": "Número de WhatsApp asignado correctamente.",
            "data": self.snapshot_serializer(assignment).data,
        }

        return Response(response_payload, status=HTTP_201_CREATED)

    def delete(self, request, company_id, branch_id, number_id):
        company = resolve_administrative_company(user=request.user, company_id=company_id)
        branch = resolve_company_branch(company=company, branch_id=branch_id)
        whatsapp_number = resolve_company_whatsapp_number(company=company, branch_id=branch.id, number_id=number_id)

        try:
            assignment = unassign_whatsapp_number(whatsapp_number=whatsapp_number, actor=request.user)

        except ValidationError as error:
            response_payload = {
                "error_message": "Desasignación de número rechazada: no fue posible completar la operación.",
                "data": error.message_dict,
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        response_payload = {
            "success_message": "Número de WhatsApp desasignado correctamente.",
            "data": self.snapshot_serializer(assignment).data,
        }

        return Response(response_payload, status=HTTP_200_OK)
