from django.conf import settings
from django.db import models
from django.db.models import Q
from common.mixins import AuthorMixin, LifeCycleMixin, TemporalMixin

# Define your models here.

class Company(TemporalMixin, LifeCycleMixin, AuthorMixin):
    """
        DOCSTRING: Company

        Description:
        - Represent a company registered in Dialoqo.
        - Establish the primary organizational and tenant boundary for company-owned data.

        Notes:
        - Companies may contain multiple branches.
        - Monitoring access must be granted explicitly through UserCompanyAccess.
        - Companies should normally be deactivated instead of destructively deleted when historical records depend on them.
    """
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "organizations_company"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["is_active"], name="idx_company_active"),
            models.Index(fields=["name"], name="idx_company_name"),
        ]

    def __str__(self):
        return self.name


class Branch(TemporalMixin, LifeCycleMixin, AuthorMixin):
    """
        DOCSTRING: Branch

        Description:
        - Represent an operational branch belonging to a Dialoqo company.

        Notes:
        - A branch belongs to exactly one company.
        - Branch codes must be unique within their company.
        - Branches should normally be deactivated instead of destructively deleted when historical records depend on them.
    """
    company = models.ForeignKey(Company, blank=False, null=False, on_delete=models.PROTECT, related_name="branches")
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "organizations_branch"
        ordering = ["company", "name"]
        indexes = [
            models.Index(fields=["company", "is_active"], name="idx_branch_company_active"),
            models.Index(fields=["company", "name"], name="idx_branch_company_name"),
        ]

    def __str__(self):
        return f"{self.company.name} - {self.name}"


class UserCompanyAccess(TemporalMixin, LifeCycleMixin, AuthorMixin):
    """
        DOCSTRING: User Company Access

        Description:
        - Represent explicit monitoring access granted to a Dialoqo user for a company.
        - Preserve company authorization history when access is later revoked.

        Notes:
        - Only MONITOR users should receive company monitoring access.
        - Only one active access record may exist for the same user and company.
        - Revoked access records must remain preserved for historical traceability.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, blank=False, null=False, on_delete=models.PROTECT, related_name="company_accesses")
    company = models.ForeignKey(Company, blank=False, null=False, on_delete=models.PROTECT, related_name="user_accesses")
    granted_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "organizations_user_company_access"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "company"],
                condition=Q(is_active=True),
                name="unique_active_user_company_access",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "is_active"], name="idx_access_user_active"),
            models.Index(fields=["company", "is_active"], name="idx_access_company_active"),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.company.name}"