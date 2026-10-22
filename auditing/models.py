from django.conf import settings
from django.db import models
from common.mixins import TemporalMixin
from organizations.models import Branch, Company
from .registry import AUDIT_ACTION_REGISTRY, AUDIT_CATEGORY_REGISTRY, AUDIT_SEVERITY_REGISTRY


# Define your auditing models here.

class AuditEvent(TemporalMixin):
    """
        DOCSTRING: Audit Event

        Description:
        - Represent an immutable business or security event recorded by Dialoqo.
        - Preserve who performed an action, where it occurred, which resource was affected, and contextual metadata required for traceability.

        Notes:
        - Audit events are append-only records.
        - actor may be null for system-generated or background events.
        - company may be null for events that occur before a tenant context exists.
        - branch is optional because many actions apply at company or system level.
        - target_app, target_model, and target_id identify the affected resource without introducing generic foreign-key dependencies.
        - metadata must never contain passwords, access tokens, refresh tokens, Meta secrets, authorization headers, cookies, or other sensitive credentials.
        - Audit records must not be modified or deleted through normal application APIs.
    """

    company = models.ForeignKey(Company, blank=True, null=True, on_delete=models.PROTECT, related_name="audit_events")
    branch = models.ForeignKey(Branch, blank=True, null=True, on_delete=models.PROTECT, related_name="audit_events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, blank=True, null=True, on_delete=models.SET_NULL, related_name="audit_events")

    category = models.CharField(max_length=30, blank=False, null=False, choices=AUDIT_CATEGORY_REGISTRY.choices)
    action = models.CharField(max_length=30, blank=False, null=False, choices=AUDIT_ACTION_REGISTRY.choices)
    severity = models.CharField(max_length=20, blank=False, null=False, choices=AUDIT_SEVERITY_REGISTRY.choices, default=AUDIT_SEVERITY_REGISTRY.INFO)

    target_app = models.CharField(max_length=100, blank=True, null=False, default="")
    target_model = models.CharField(max_length=150, blank=True, null=False, default="")
    target_id = models.CharField(max_length=100, blank=True, null=False, default="")
    target_label = models.CharField(max_length=255, blank=True, null=False, default="")

    description = models.TextField(blank=False, null=False)
    metadata = models.JSONField(blank=True, null=False, default=dict)

    request_id = models.CharField(max_length=100, blank=True, null=False, default="")
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.TextField(blank=True, null=False, default="")

    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "auditing_event"
        verbose_name = "Audit event"
        verbose_name_plural = "Audit events"
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["company", "occurred_at"], name="idx_audit_company_time"),
            models.Index(fields=["company", "category"], name="idx_audit_company_category"),
            models.Index(fields=["company", "action"], name="idx_audit_company_action"),
            models.Index(fields=["company", "severity"], name="idx_audit_company_severity"),
            models.Index(fields=["actor", "occurred_at"], name="idx_audit_actor_time"),
            models.Index(fields=["branch", "occurred_at"], name="idx_audit_branch_time"),
            models.Index(fields=["target_app", "target_model", "target_id"], name="idx_audit_target"),
            models.Index(fields=["request_id"], name="idx_audit_request"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise RuntimeError("Actualización de auditoría rechazada: los eventos de auditoría son inmutables.")

        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Eliminación de auditoría rechazada: los eventos de auditoría son inmutables.")

    def __str__(self):
        actor_label = str(self.actor) if self.actor else "SYSTEM"
        return f"{self.category} - {self.action} - {actor_label}"
