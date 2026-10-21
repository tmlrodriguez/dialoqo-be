from rest_framework import serializers
from access.serializers.snapshots import AccessUserSnapshotSerializer
from ..models import Branch, Company, UserCompanyAccess

# Define your serializers here.

class CompanySnapshotSerializer(serializers.ModelSerializer):
    """
        DOCSTRING: Company Snapshot Serializer

        Description:
        - Provide a compact read-only representation of a Dialoqo company.

        Notes:
        - Snapshot serializers are intended for lightweight and nested representations.
        - Snapshot serializers must remain read-only.
    """

    class Meta:
        model = Company
        fields = ["id", "name", "description", "is_active"]
        read_only_fields = fields

class BranchSnapshotSerializer(serializers.ModelSerializer):
    """
        DOCSTRING: Branch Snapshot Serializer

        Description:
        - Provide a compact read-only representation of a Dialoqo branch.

        Notes:
        - The company relationship is represented through CompanySnapshotSerializer.
        - Snapshot serializers must remain read-only.
    """

    company = CompanySnapshotSerializer(read_only=True)

    class Meta:
        model = Branch
        fields = ["id", "company", "name", "description", "is_active"]
        read_only_fields = fields


class UserCompanyAccessSnapshotSerializer(serializers.ModelSerializer):
    """
        DOCSTRING: User Company Access Snapshot Serializer

        Description:
        - Provide a compact read-only representation of a company monitoring access assignment.

        Notes:
        - The user relationship is represented through AccessUserSnapshotSerializer.
        - The company relationship is represented through CompanySnapshotSerializer.
        - Snapshot serializers must remain read-only.
    """
    user = AccessUserSnapshotSerializer(read_only=True)
    company = CompanySnapshotSerializer(read_only=True)

    class Meta:
        model = UserCompanyAccess
        fields = ["id", "user", "company", "is_active", "granted_at", "revoked_at"]
        read_only_fields = fields