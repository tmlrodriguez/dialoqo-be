from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from common.mixins import AuthorMixin, LifeCycleMixin, TemporalMixin
from organizations.models import Branch, Company
from .registry import MEDIA_STORAGE_STATUS_REGISTRY, MESSAGE_DIRECTION_REGISTRY, MESSAGE_STATUS_REGISTRY, MESSAGE_TEMPLATE_CATEGORY_REGISTRY, MESSAGE_TEMPLATE_PARAMETER_FORMAT_REGISTRY, MESSAGE_TEMPLATE_STATUS_REGISTRY, MESSAGE_TYPE_REGISTRY

# Define your models here.

class WhatsAppBusinessAccount(TemporalMixin, LifeCycleMixin, AuthorMixin):
    """
        DOCSTRING: WhatsApp Business Account

        Description:
        - Represent a Meta WhatsApp Business Account configured in Dialoqo.
        - Group one or more corporate WhatsApp numbers under the corresponding company.

        Notes:
        - A WhatsApp Business Account belongs to exactly one company.
        - Meta application credentials are global platform configuration and are never stored per company.
        - Records are deactivated instead of destructively deleted.
    """

    company = models.ForeignKey(Company, blank=False, null=False, on_delete=models.PROTECT, related_name="whatsapp_business_accounts")
    meta_waba_id = models.CharField(max_length=100, blank=False, null=False, unique=True)
    meta_business_id = models.CharField(max_length=100, blank=False, null=False)
    display_name = models.CharField(max_length=150, blank=False, null=False)
    is_connected = models.BooleanField(default=False)
    is_webhook_configured = models.BooleanField(default=False)
    connected_at = models.DateTimeField(blank=True, null=True)
    disconnected_at = models.DateTimeField(blank=True, null=True)
    notes = models.TextField(blank=True, null=False, default="")

    class Meta:
        db_table = "whatsapp_business_account"
        ordering = ["display_name"]
        indexes = [
            models.Index(fields=["company", "is_active"], name="idx_waba_company_active"),
            models.Index(fields=["company", "is_connected"], name="idx_waba_company_connected"),
        ]

    def __str__(self):
        return self.display_name


class WhatsAppMessageTemplate(TemporalMixin, LifeCycleMixin, AuthorMixin):
    """
        DOCSTRING: WhatsApp Message Template

        Description:
        - Represent a WhatsApp message template belonging to a configured WhatsApp Business Account.
        - Persist the latest template definition synchronized from Meta.
        - Provide the authoritative local template configuration used by Dialoqo outbound messaging.

        Notes:
        - A template belongs to exactly one WhatsApp Business Account.
        - Template names and languages are unique within the owning WhatsApp Business Account.
        - meta_template_id identifies the corresponding Meta template resource.
        - Only APPROVED templates may be used for outbound template messaging.
        - components stores the normalized Meta template component definition.
        - quality_score stores Meta quality information when available.
        - last_synced_at records the latest successful synchronization from Meta.
        - is_available_in_meta indicates whether the template was present during the latest successful synchronization.
        - removed_from_meta_at preserves when a previously known template disappeared from Meta.
        - Template records are preserved instead of destructively deleted when historical messages depend on them.
    """

    whatsapp_business_account = models.ForeignKey(WhatsAppBusinessAccount, blank=False, null=False, on_delete=models.PROTECT, related_name="message_templates")
    meta_template_id = models.CharField(max_length=100, blank=False, null=False, unique=True)
    name = models.CharField(max_length=512, blank=False, null=False)
    language = models.CharField(max_length=50, blank=False, null=False)
    category = models.CharField(max_length=30, blank=False, null=False, choices=MESSAGE_TEMPLATE_CATEGORY_REGISTRY.choices, default=MESSAGE_TEMPLATE_CATEGORY_REGISTRY.UNKNOWN)
    status = models.CharField(max_length=30, blank=False, null=False, choices=MESSAGE_TEMPLATE_STATUS_REGISTRY.choices, default=MESSAGE_TEMPLATE_STATUS_REGISTRY.UNKNOWN)
    parameter_format = models.CharField(max_length=20, blank=False, null=False, choices=MESSAGE_TEMPLATE_PARAMETER_FORMAT_REGISTRY.choices, default=MESSAGE_TEMPLATE_PARAMETER_FORMAT_REGISTRY.POSITIONAL)
    components = models.JSONField(blank=True, null=False, default=list)
    quality_score = models.JSONField(blank=True, null=False, default=dict)
    rejection_reason = models.TextField(blank=True, null=False, default="")
    is_available_in_meta = models.BooleanField(default=True)
    last_synced_at = models.DateTimeField(blank=True, null=True)
    removed_from_meta_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "whatsapp_message_template"
        ordering = ["name", "language"]
        constraints = [
            models.UniqueConstraint(
                fields=["whatsapp_business_account", "name", "language"],
                name="unique_waba_template_language",
            ),
        ]
        indexes = [
            models.Index(fields=["whatsapp_business_account", "status"], name="idx_tpl_waba_status"),
            models.Index(fields=["whatsapp_business_account", "category"], name="idx_tpl_waba_category"),
            models.Index(fields=["whatsapp_business_account", "language"], name="idx_tpl_waba_language"),
            models.Index(fields=["whatsapp_business_account", "is_available_in_meta"], name="idx_tpl_waba_available"),
        ]

    def __str__(self):
        return f"{self.name} - {self.language}"


class WhatsAppNumber(TemporalMixin, LifeCycleMixin, AuthorMixin):
    """
        DOCSTRING: WhatsApp Number

        Description:
        - Represent a corporate WhatsApp number registered in Dialoqo.
        - Associate the number with its company, branch, and WhatsApp Business Account.

        Notes:
        - The branch and WhatsApp Business Account must belong to the same company as the number.
        - meta_phone_number_id uniquely identifies the number in Meta.
        - Monitoring begins only through an explicit monitoring activation operation.
        - Historical WhatsApp synchronization before monitoring_started_at is outside the MVP.
        - Records are deactivated instead of destructively deleted.
    """

    company = models.ForeignKey(Company, blank=False, null=False, on_delete=models.PROTECT, related_name="whatsapp_numbers")
    branch = models.ForeignKey(Branch, blank=False, null=False, on_delete=models.PROTECT, related_name="whatsapp_numbers")
    whatsapp_business_account = models.ForeignKey(WhatsAppBusinessAccount, blank=False, null=False, on_delete=models.PROTECT, related_name="numbers")
    phone_number = models.CharField(max_length=30, blank=False, null=False, unique=True)
    display_name = models.CharField(max_length=150, blank=False, null=False)
    meta_phone_number_id = models.CharField(max_length=100, blank=False, null=False, unique=True)
    is_connected = models.BooleanField(default=False)
    is_monitoring_enabled = models.BooleanField(default=False)
    monitoring_started_at = models.DateTimeField(blank=True, null=True)
    monitoring_stopped_at = models.DateTimeField(blank=True, null=True)
    notes = models.TextField(blank=True, null=False, default="")

    class Meta:
        db_table = "whatsapp_number"
        ordering = ["display_name"]
        indexes = [
            models.Index(fields=["company", "is_active"], name="idx_wa_number_company_active"),
            models.Index(fields=["branch", "is_active"], name="idx_wa_number_branch_active"),
            models.Index(fields=["whatsapp_business_account", "is_active"], name="idx_wa_number_waba_active"),
            models.Index(fields=["meta_phone_number_id"], name="idx_wa_number_meta_id"),
            models.Index(fields=["company", "is_monitoring_enabled"], name="idx_wa_number_monitoring"),
        ]

    def __str__(self):
        return f"{self.display_name} - {self.phone_number}"


class NumberAssignment(TemporalMixin, LifeCycleMixin, AuthorMixin):
    """
        DOCSTRING: Number Assignment

        Description:
        - Represent the historical assignment of a corporate WhatsApp number to a Dialoqo MEMBER user.
        - Preserve responsibility history when a WhatsApp number is reassigned.

        Notes:
        - Only one active assignment may exist for a WhatsApp number.
        - Only active AccessUser records with role MEMBER may receive number assignments.
        - Assignment eligibility and administrative ownership are enforced by serializers and operations.
        - Reassignment must close the existing assignment before creating the new assignment.
        - Historical assignments must never be overwritten or deleted.
    """

    whatsapp_number = models.ForeignKey(WhatsAppNumber, blank=False, null=False, on_delete=models.PROTECT, related_name="assignments")
    member = models.ForeignKey(settings.AUTH_USER_MODEL, blank=False, null=False, on_delete=models.PROTECT, related_name="whatsapp_number_assignments")
    assigned_at = models.DateTimeField(default=timezone.now)
    unassigned_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "whatsapp_number_assignment"
        ordering = ["-assigned_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["whatsapp_number"],
                condition=Q(is_active=True),
                name="unique_active_whatsapp_number_assignment",
            ),
            models.CheckConstraint(
                condition=Q(unassigned_at__isnull=True) | Q(unassigned_at__gte=models.F("assigned_at")),
                name="assignment_unassigned_after_assigned",
            ),
        ]
        indexes = [
            models.Index(fields=["whatsapp_number", "is_active"], name="idx_assignment_number_active"),
            models.Index(fields=["member", "is_active"], name="idx_assignment_member_active"),
            models.Index(fields=["whatsapp_number", "assigned_at"], name="idx_assignment_number_date"),
        ]

    def __str__(self):
        return f"{self.whatsapp_number.phone_number} - {self.member.username}"


class Customer(TemporalMixin, LifeCycleMixin, AuthorMixin):
    """
        DOCSTRING: Customer

        Description:
        - Represent the external WhatsApp participant communicating with a company-owned WhatsApp number.
        - Provide a minimal company-scoped identity used to organize monitored conversations.

        Notes:
        - Customer records are not CRM records.
        - A customer belongs to exactly one company context.
        - The same phone number may exist independently under different companies.
        - Customer phone numbers must be unique within each company.
        - The model must remain limited to identity information required for conversation monitoring.
    """

    company = models.ForeignKey(Company, blank=False, null=False, on_delete=models.PROTECT, related_name="customers")
    phone_number = models.CharField(max_length=30, blank=False, null=False)
    display_name = models.CharField(max_length=150, blank=True, null=False, default="")
    profile_name = models.CharField(max_length=150, blank=True, null=False, default="")

    class Meta:
        db_table = "whatsapp_customer"
        ordering = ["phone_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "phone_number"],
                name="unique_customer_phone_per_company",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "is_active"], name="idx_customer_company_active"),
            models.Index(fields=["company", "phone_number"], name="idx_customer_company_phone"),
        ]

    def __str__(self):
        return self.display_name or self.profile_name or self.phone_number


class Conversation(TemporalMixin, LifeCycleMixin, AuthorMixin):
    """
        DOCSTRING: Conversation

        Description:
        - Represent the monitored conversation between one corporate WhatsApp number and one customer.
        - Provide the parent record used to organize persisted monitored messages.
        - Maintain the latest message information required for efficient conversation listing.

        Notes:
        - A conversation belongs to exactly one WhatsApp number and one customer.
        - The customer must belong to the same company as the WhatsApp number.
        - Only one conversation may exist for the same WhatsApp number and customer.
        - last_message references the most recently persisted message in the conversation.
        - last_message_at supports efficient ordering without aggregating the complete message history.
        - Read and unread state is maintained independently per monitoring user through ConversationReadState.
    """

    whatsapp_number = models.ForeignKey(WhatsAppNumber, blank=False, null=False, on_delete=models.PROTECT, related_name="conversations")
    customer = models.ForeignKey(Customer, blank=False, null=False, on_delete=models.PROTECT, related_name="conversations")
    last_message = models.ForeignKey("Message", blank=True, null=True, on_delete=models.SET_NULL, related_name="+")
    last_message_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "whatsapp_conversation"
        ordering = ["-last_message_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["whatsapp_number", "customer"],
                name="unique_conversation_per_number_customer",
            ),
        ]
        indexes = [
            models.Index(fields=["whatsapp_number", "is_active"], name="idx_conversation_number_active"),
            models.Index(fields=["whatsapp_number", "last_message_at"], name="idx_conversation_number_last"),
            models.Index(fields=["customer", "last_message_at"], name="idx_conversation_customer_last"),
        ]

    def __str__(self):
        return f"{self.whatsapp_number.phone_number} - {self.customer.phone_number}"


class Message(TemporalMixin, LifeCycleMixin, AuthorMixin):
    """
        DOCSTRING: Message

        Description:
        - Represent a monitored WhatsApp message persisted by Dialoqo.
        - Store normalized message information independently from the raw Meta webhook payload.
        - Maintain delivery, editing, revocation, structured content, and message-context information.

        Notes:
        - Messages belong to exactly one conversation.
        - meta_message_id provides the primary idempotency key for Meta message persistence.
        - INBOUND represents customer to business.
        - OUTBOUND represents business to customer.
        - content_data stores normalized structured content required for non-text message rendering.
        - meta_context_message_id preserves the referenced Meta message identifier when a message replies to or reacts to another message.
        - context_message links the referenced message when that message is available locally.
        - original_text_body preserves the first visible text before an edit.
        - revoked_at causes serializers to hide original message content and render the deleted-message representation.
        - Duplicate Meta webhook deliveries must not create duplicate message records.
    """

    conversation = models.ForeignKey(Conversation, blank=False, null=False, on_delete=models.PROTECT, related_name="messages")
    meta_message_id = models.CharField(max_length=255, blank=False, null=False, unique=True)
    direction = models.CharField(max_length=20, blank=False, null=False, choices=MESSAGE_DIRECTION_REGISTRY.choices)
    message_type = models.CharField(max_length=20, blank=False, null=False, choices=MESSAGE_TYPE_REGISTRY.choices)
    status = models.CharField(max_length=20, blank=False, null=False, choices=MESSAGE_STATUS_REGISTRY.choices, default=MESSAGE_STATUS_REGISTRY.RECEIVED)
    sender_phone_number = models.CharField(max_length=30, blank=False, null=False)
    recipient_phone_number = models.CharField(max_length=30, blank=False, null=False)
    text_body = models.TextField(blank=True, null=False, default="")
    original_text_body = models.TextField(blank=True, null=False, default="")
    content_data = models.JSONField(blank=True, null=False, default=dict)
    meta_context_message_id = models.CharField(max_length=255, blank=True, null=False, default="")
    context_message = models.ForeignKey("self", blank=True, null=True, on_delete=models.SET_NULL, related_name="context_events")
    message_timestamp = models.DateTimeField(blank=False, null=False)
    meta_event_timestamp = models.DateTimeField(blank=False, null=False)
    status_updated_at = models.DateTimeField(blank=True, null=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    delivered_at = models.DateTimeField(blank=True, null=True)
    read_at = models.DateTimeField(blank=True, null=True)
    failed_at = models.DateTimeField(blank=True, null=True)
    failure_code = models.CharField(max_length=100, blank=True, null=False, default="")
    failure_message = models.TextField(blank=True, null=False, default="")
    edited_at = models.DateTimeField(blank=True, null=True)
    revoked_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "whatsapp_message"
        ordering = ["message_timestamp", "id"]
        indexes = [
            models.Index(fields=["conversation", "message_timestamp"], name="idx_message_conversation_time"),
            models.Index(fields=["conversation", "status"], name="idx_message_conv_status"),
            models.Index(fields=["direction", "message_timestamp"], name="idx_message_direction_time"),
            models.Index(fields=["message_type", "message_timestamp"], name="idx_message_type_time"),
            models.Index(fields=["status", "message_timestamp"], name="idx_message_status_time"),
            models.Index(fields=["meta_context_message_id"], name="idx_message_context_meta"),
        ]

    @property
    def is_edited(self):
        return self.edited_at is not None

    @property
    def is_revoked(self):
        return self.revoked_at is not None

    @property
    def display_text(self):
        if self.is_revoked:
            return "Este mensaje fue eliminado."

        return self.text_body

    def __str__(self):
        return self.meta_message_id


class MediaAttachment(TemporalMixin, LifeCycleMixin, AuthorMixin):
    """
        DOCSTRING: Media Attachment

        Description:
        - Represent metadata and private-storage state for media attached to a monitored WhatsApp message.
        - Reference privately stored binary content without storing large files directly in PostgreSQL.

        Notes:
        - Binary content must be stored through private application storage.
        - storage_key identifies the private storage object and must never be exposed through normal API responses.
        - Meta media identifiers are used to retrieve media from WhatsApp Business Platform.
        - Permanent public media URLs must not be stored.
        - Storage failures remain retryable.
    """

    message = models.ForeignKey(Message, blank=False, null=False, on_delete=models.PROTECT, related_name="media_attachments")
    meta_media_id = models.CharField(max_length=255, blank=False, null=False)
    storage_key = models.CharField(max_length=500, blank=True, null=False, default="")
    storage_status = models.CharField(max_length=20, blank=False, null=False, choices=MEDIA_STORAGE_STATUS_REGISTRY.choices, default=MEDIA_STORAGE_STATUS_REGISTRY.PENDING)
    mime_type = models.CharField(max_length=150, blank=False, null=False)
    size = models.BigIntegerField(blank=True, null=True)
    original_filename = models.CharField(max_length=255, blank=True, null=False, default="")
    checksum = models.CharField(max_length=255, blank=True, null=False, default="")
    is_stored = models.BooleanField(default=False)
    stored_at = models.DateTimeField(blank=True, null=True)
    storage_failed_at = models.DateTimeField(blank=True, null=True)
    storage_error = models.TextField(blank=True, null=False, default="")
    retrieval_attempts = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "whatsapp_media_attachment"
        constraints = [
            models.UniqueConstraint(
                fields=["message", "meta_media_id"],
                name="unique_media_per_message",
            ),
        ]
        indexes = [
            models.Index(fields=["message", "is_active"], name="idx_media_message_active"),
            models.Index(fields=["meta_media_id"], name="idx_media_meta_id"),
            models.Index(fields=["is_stored"], name="idx_media_stored"),
            models.Index(fields=["storage_status"], name="idx_media_storage_status"),
        ]

    def __str__(self):
        return self.original_filename or self.meta_media_id


class ConversationReadState(TemporalMixin):
    """
        DOCSTRING: Conversation Read State

        Description:
        - Maintain the read and unread state of a conversation independently for each monitoring user.
        - Support WhatsApp-style unread indicators in the conversation list.

        Notes:
        - Read state belongs to a specific user and conversation combination.
        - Opening a conversation marks it as read only for the authenticated monitoring user.
        - A new inbound message marks the conversation as unread for applicable monitoring users.
        - last_read_message identifies the most recent message acknowledged by the monitoring user.
        - unread_count supports displaying an unread message badge without recalculating the complete message history.
    """

    conversation = models.ForeignKey(Conversation, blank=False, null=False, on_delete=models.CASCADE, related_name="read_states")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, blank=False, null=False, on_delete=models.CASCADE, related_name="whatsapp_conversation_read_states")
    is_read = models.BooleanField(default=False)
    unread_count = models.PositiveIntegerField(default=0)
    last_read_message = models.ForeignKey(Message, blank=True, null=True, on_delete=models.SET_NULL, related_name="+")
    last_opened_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "whatsapp_conversation_read_state"
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "user"],
                name="unique_conversation_read_state_per_user",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "is_read"], name="idx_read_state_user_read"),
            models.Index(fields=["conversation", "user"], name="idx_readstate_conv_user"),
        ]

    def __str__(self):
        return f"{self.user_id} - {self.conversation_id}"


class MetaCredential(TemporalMixin, LifeCycleMixin, AuthorMixin):
    """
        DOCSTRING: Meta Credential

        Description:
        - Store encrypted operational Meta credentials associated
          with a specific WhatsApp Business Account.
        - Support secure credential resolution for multiple
          customer Business Portfolios.

        Notes:
        - Each WABA has at most one credential record.
        - Access tokens are encrypted before persistence.
        - Plaintext tokens must never be stored.
        - Credentials are never exposed through serializers.
        - Credential replacement preserves the record identity.
        - Revocation clears the encrypted token.
        - The existing global System User token remains available
          for legacy integrations during development.
    """

    whatsapp_business_account = models.OneToOneField(
        WhatsAppBusinessAccount,
        on_delete=models.PROTECT,
        related_name="meta_credential",
    )

    encrypted_access_token = models.TextField(
        blank=True,
        default="",
    )

    is_valid = models.BooleanField(
        default=True,
    )

    last_validated_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    expires_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    class Meta:
        db_table = "whatsapp_meta_credential"

        indexes = [
            models.Index(
                fields=["is_active"],
                name="idx_meta_credential_active",
            ),
            models.Index(
                fields=["is_valid"],
                name="idx_meta_credential_valid",
            ),
        ]

    def __str__(self):
        return (
            f"Meta Credential - "
            f"WABA {self.whatsapp_business_account_id}"
        )