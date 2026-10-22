from django.db import models
from common.mixins import AuthorMixin, LifeCycleMixin, TemporalMixin
from organizations.models import Branch, Company

# Define your models here.

class Position(TemporalMixin, LifeCycleMixin, AuthorMixin):
    """
        DOCSTRING: Position

        Description:
        - Represent an operational position available to members within a Dialoqo company.
        - Provide a reusable company-scoped position definition for member records.

        Notes:
        - A position belongs to exactly one company.
        - Position codes must be unique within each company.
        - Position records are deactivated instead of destructively deleted.
        - The model is intended only for operational member classification.
    """
    company = models.ForeignKey(Company, blank=False, null=False, on_delete=models.PROTECT, related_name="positions")
    name = models.CharField(max_length=150, blank=False, null=False)
    code = models.CharField(max_length=50, blank=False, null=False)
    description = models.TextField(blank=True, null=False, default="")

    class Meta:
        db_table = "members_position"
        verbose_name = "Position"
        verbose_name_plural = "Positions"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"],
                name="unique_position_code_per_company",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "is_active"], name="idx_position_company_active"),
            models.Index(fields=["company", "code"], name="idx_position_company_code"),
        ]

    def __str__(self):
        return self.name


class Member(TemporalMixin, LifeCycleMixin, AuthorMixin):
    """
        DOCSTRING: Member

        Description:
        - Represent a company member registered in Dialoqo.
        - Associate the member with a company, branch, and operational position.

        Notes:
        - Every member belongs to exactly one company.
        - Every member belongs to exactly one branch.
        - Every member belongs to exactly one position.
        - The branch and position must belong to the same company as the member.
        - Member codes must be unique within each company.
        - Member records are deactivated instead of destructively deleted.
        - The model is intended for operational identification and not full HR management.
    """
    company = models.ForeignKey(Company, blank=False, null=False, on_delete=models.PROTECT, related_name="members")
    branch = models.ForeignKey(Branch, blank=False, null=False, on_delete=models.PROTECT, related_name="members")
    position = models.ForeignKey(Position, blank=False, null=False, on_delete=models.PROTECT, related_name="members")
    member_code = models.CharField(max_length=50, blank=False, null=False)
    first_name = models.CharField(max_length=150, blank=False, null=False)
    last_name = models.CharField(max_length=150, blank=False, null=False)
    email = models.EmailField(blank=False, null=False)
    notes = models.TextField(blank=True, null=False, default="")

    class Meta:
        db_table = "members_member"
        verbose_name = "Member"
        verbose_name_plural = "Members"
        ordering = ["first_name", "last_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "member_code"],
                name="unique_member_code_per_company",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "is_active"], name="idx_member_company_active"),
            models.Index(fields=["branch", "is_active"], name="idx_member_branch_active"),
            models.Index(fields=["position", "is_active"], name="idx_member_position_active"),
            models.Index(fields=["company", "member_code"], name="idx_member_company_code"),
        ]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"
