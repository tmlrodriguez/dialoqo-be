from django.contrib.auth.models import AbstractUser
from django.db import models
from .registry import ROLE_REGISTRY
from .managers import AccessUserManager

# Define your models here.

class AccessUser(AbstractUser):
    """
        DOCSTRING: AccessUser

        Description:
        - Represent an authenticated Dialoqo user.
        - Store the application role assigned to the user.
        - Preserve which Dialoqo user created and last updated the account.

        Notes:
        - SUPERADMINISTRATOR users may create ADMINISTRATOR users.
        - ADMINISTRATOR users may create MONITOR users.
        - MONITOR users receive company access separately.
        - Administrative roles do not automatically grant access to monitored conversation content.
        - Author relationships are optional to support platform-created and historical users.
    """

    role = models.CharField(max_length=25, blank=False, null=False, choices=ROLE_REGISTRY.choices)
    created_by = models.ForeignKey("self", blank=True, null=True, on_delete=models.PROTECT, related_name="created_users")
    updated_by = models.ForeignKey("self", blank=True, null=True, on_delete=models.PROTECT, related_name="updated_users")

    objects = AccessUserManager()

    class Meta:
        db_table = "access_user"
        verbose_name = "Access user"
        verbose_name_plural = "Access users"

    def __str__(self):
        return self.username
