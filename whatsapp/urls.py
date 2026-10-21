from django.urls import path

from .integration_views import (
    WhatsAppBusinessAccountConnectionView,
    WhatsAppEmbeddedSignupView,
    WhatsAppNumberValidationView,
)

from .media_views import (
    MediaAttachmentContentView,
)

from .template_views import (
    AvailableMessageTemplateView,
    ConversationTemplateSendView,
    MessageTemplateSyncView,
    MessageTemplateView,
    NewConversationTemplateSendView,
)

from .views import (
    ConversationReadView,
    ConversationView,
    MediaAttachmentView,
    MessageView,
    MetaWebhookView,
    MonitoringContextView,
    NumberAssignmentView,
    OutboundMessageView,
    WhatsAppBusinessAccountView,
    WhatsAppMonitoringView,
    WhatsAppNumberView,
)


# Define your URLs here.

app_name = "whatsapp"


urlpatterns = [

    # ---------------------------------------------------------
    # WhatsApp Business Accounts
    # ---------------------------------------------------------

    path(
        "companies/<int:company_id>/accounts/",
        WhatsAppBusinessAccountView.as_view(),
        name="account-list-create",
    ),

    path(
        "companies/<int:company_id>/accounts/<int:account_id>/",
        WhatsAppBusinessAccountView.as_view(),
        name="account-detail",
    ),

    path(
        "companies/<int:company_id>/accounts/<int:account_id>/connection/",
        WhatsAppBusinessAccountConnectionView.as_view(),
        name="account-connection",
    ),

    # ---------------------------------------------------------
    # Meta Embedded Signup
    # ---------------------------------------------------------

    path(
        "companies/<int:company_id>/embedded-signup/",
        WhatsAppEmbeddedSignupView.as_view(),
        name="embedded-signup",
    ),

    # ---------------------------------------------------------
    # WhatsApp Message Templates
    # ---------------------------------------------------------

    path(
        "companies/<int:company_id>/accounts/<int:account_id>/templates/",
        MessageTemplateView.as_view(),
        name="template-list-create",
    ),

    path(
        "companies/<int:company_id>/accounts/<int:account_id>/templates/<int:template_id>/",
        MessageTemplateView.as_view(),
        name="template-detail",
    ),

    path(
        "companies/<int:company_id>/accounts/<int:account_id>/templates/sync/",
        MessageTemplateSyncView.as_view(),
        name="template-sync",
    ),

    # ---------------------------------------------------------
    # WhatsApp Numbers
    # ---------------------------------------------------------

    path(
        "companies/<int:company_id>/branches/<int:branch_id>/numbers/",
        WhatsAppNumberView.as_view(),
        name="number-list-create",
    ),

    path(
        "companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/",
        WhatsAppNumberView.as_view(),
        name="number-detail",
    ),

    path(
        "companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/validate/",
        WhatsAppNumberValidationView.as_view(),
        name="number-validate",
    ),

    path(
        "companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/monitoring/",
        WhatsAppMonitoringView.as_view(),
        name="number-monitoring",
    ),

    # ---------------------------------------------------------
    # Available Templates and New Conversations
    # ---------------------------------------------------------

    path(
        "companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/templates/",
        AvailableMessageTemplateView.as_view(),
        name="available-template-list",
    ),

    path(
        "companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/templates/send/",
        NewConversationTemplateSendView.as_view(),
        name="template-new-conversation-send",
    ),

    # ---------------------------------------------------------
    # Number Assignments
    # ---------------------------------------------------------

    path(
        "companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/assignments/",
        NumberAssignmentView.as_view(),
        name="assignment-list-create",
    ),

    path(
        "companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/assignments/<int:assignment_id>/",
        NumberAssignmentView.as_view(),
        name="assignment-detail",
    ),

    # ---------------------------------------------------------
    # Monitoring Context
    # ---------------------------------------------------------

    path(
        "monitoring/context/",
        MonitoringContextView.as_view(),
        name="monitoring-context",
    ),

    # ---------------------------------------------------------
    # Conversations
    # ---------------------------------------------------------

    path(
        "companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/conversations/",
        ConversationView.as_view(),
        name="conversation-list",
    ),

    path(
        "companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/conversations/<int:conversation_id>/",
        ConversationView.as_view(),
        name="conversation-detail",
    ),

    path(
        "companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/conversations/<int:conversation_id>/read/",
        ConversationReadView.as_view(),
        name="conversation-read",
    ),

    # ---------------------------------------------------------
    # Messages
    # ---------------------------------------------------------

    path(
        "companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/conversations/<int:conversation_id>/messages/",
        MessageView.as_view(),
        name="message-list",
    ),

    path(
        "companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/conversations/<int:conversation_id>/messages/send/",
        OutboundMessageView.as_view(),
        name="message-send",
    ),

    path(
        "companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/conversations/<int:conversation_id>/messages/<int:message_id>/",
        MessageView.as_view(),
        name="message-detail",
    ),

    # ---------------------------------------------------------
    # Conversation Template Messaging
    # ---------------------------------------------------------

    path(
        "companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/conversations/<int:conversation_id>/templates/send/",
        ConversationTemplateSendView.as_view(),
        name="conversation-template-send",
    ),

    # ---------------------------------------------------------
    # Media Attachments
    # ---------------------------------------------------------

    path(
        "companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/conversations/<int:conversation_id>/messages/<int:message_id>/attachments/",
        MediaAttachmentView.as_view(),
        name="attachment-list",
    ),

    path(
        "companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/conversations/<int:conversation_id>/messages/<int:message_id>/attachments/<int:attachment_id>/",
        MediaAttachmentView.as_view(),
        name="attachment-detail",
    ),

    path(
        "companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/conversations/<int:conversation_id>/messages/<int:message_id>/attachments/<int:attachment_id>/content/",
        MediaAttachmentContentView.as_view(),
        name="attachment-content",
    ),

    # ---------------------------------------------------------
    # Meta Webhooks
    # ---------------------------------------------------------

    path(
        "webhooks/",
        MetaWebhookView.as_view(),
        name="meta-webhook",
    ),
]