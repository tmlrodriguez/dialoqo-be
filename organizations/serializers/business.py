from rest_framework import serializers
from access.registry import ROLE_REGISTRY
from access.serializers.snapshots import AccessUserSnapshotSerializer
from ..models import Branch, Company, UserCompanyAccess

# Define your serializers here.

class CompanySerializer(serializers.ModelSerializer):
    """
        DOCSTRING: Company Serializer

        Description:
        - Serialize and validate the primary Dialoqo company resource.

        Notes:
        - Company lifecycle is represented through is_active.
        - Temporal and author fields are controlled exclusively by the backend.
        - Author relationships are represented through AccessUser snapshots.
    """
    created_by = AccessUserSnapshotSerializer(read_only=True)
    updated_by = AccessUserSnapshotSerializer(read_only=True)

    class Meta:
        model = Company
        fields = ["id", "name", "description", "is_active", "created_at", "updated_at", "created_by", "updated_by"]
        read_only_fields = ["id", "created_at", "updated_at", "created_by", "updated_by"]


class BranchSerializer(serializers.ModelSerializer):
    """
        DOCSTRING: Branch Serializer

        Description:
        - Serialize and validate the primary Dialoqo branch resource.

        Notes:
        - A branch must belong to an existing active company.
        - Temporal and author fields are controlled exclusively by the backend.
        - Author relationships are represented through AccessUser snapshots.
    """
    created_by = AccessUserSnapshotSerializer(read_only=True)
    updated_by = AccessUserSnapshotSerializer(read_only=True)

    class Meta:
        model = Branch
        fields = ["id", "company", "name", "description", "is_active", "created_at", "updated_at", "created_by", "updated_by"]
        read_only_fields = ["id", "created_at", "updated_at", "created_by", "updated_by"]

    def validate_company(self, value):
        if not value.is_active:
            raise serializers.ValidationError("Asignación de empresa rechazada: la empresa seleccionada se encuentra inactiva.")
        return value


class UserCompanyAccessSerializer(serializers.ModelSerializer):
    """
        DOCSTRING: User Company Access Serializer

        Description:
        - Serialize and validate monitoring access assigned to a Dialoqo user for a company.

        Notes:
        - Only MONITOR users may receive company monitoring access.
        - Only active companies may receive new monitoring access assignments.
        - Lifecycle, temporal, author, and access lifecycle fields are controlled by the backend.
        - Duplicate active access assignments must be rejected.
    """
    created_by = AccessUserSnapshotSerializer(read_only=True)
    updated_by = AccessUserSnapshotSerializer(read_only=True)

    class Meta:
        model = UserCompanyAccess
        fields = ["id", "user", "company", "is_active", "granted_at", "revoked_at", "created_at", "updated_at", "created_by", "updated_by"]
        read_only_fields = ["id", "is_active", "granted_at", "revoked_at", "created_at", "updated_at", "created_by", "updated_by"]

    def validate_user(self, value):
        if value.role != ROLE_REGISTRY.MONITOR:
            raise serializers.ValidationError("Asignación de acceso rechazada: el usuario debe tener el rol MONITOR.")
        if not value.is_active:
            raise serializers.ValidationError("Asignación de acceso rechazada: el usuario seleccionado se encuentra inactivo.")
        return value

    def validate_company(self, value):
        if not value.is_active:
            raise serializers.ValidationError("Asignación de acceso rechazada: la empresa seleccionada se encuentra inactiva.")
        return value

    def validate(self, attrs):
        user = attrs.get("user")
        company = attrs.get("company")
        if UserCompanyAccess.objects.filter(user=user, company=company, is_active=True).exists():
            raise serializers.ValidationError("Asignación de acceso rechazada: el usuario ya tiene acceso activo a esta empresa.")
        return attrs