from django.urls import path

from whatsapp.views.accounts import WhatsAppBusinessAccountView
from whatsapp.views.assignments import NumberAssignmentView
from whatsapp.views.conversations import ConversationReadView, ConversationView
from whatsapp.views.embedded_signup import WhatsAppEmbeddedSignupView
from whatsapp.views.integration import WhatsAppBusinessAccountConnectionView, WhatsAppNumberValidationView
from whatsapp.views.media import MediaAttachmentContentView, MediaAttachmentView
from whatsapp.views.messages import MessageView, OutboundMessageView
from whatsapp.views.monitoring import MonitoringContextView, WhatsAppMonitoringView
from whatsapp.views.numbers import WhatsAppNumberView
from whatsapp.views.templates import AvailableMessageTemplateView, ConversationTemplateSendView, MessageTemplateSyncView, MessageTemplateView, NewConversationTemplateSendView
from whatsapp.views.webhooks import MetaWebhookView

app_name = "whatsapp"

urlpatterns = [
    path("companies/<int:company_id>/accounts/", WhatsAppBusinessAccountView.as_view(), name="account-list-create"),
    path("companies/<int:company_id>/accounts/<int:account_id>/", WhatsAppBusinessAccountView.as_view(), name="account-detail"),
    path("companies/<int:company_id>/accounts/<int:account_id>/connection/", WhatsAppBusinessAccountConnectionView.as_view(), name="account-connection"),

    path("companies/<int:company_id>/embedded-signup/", WhatsAppEmbeddedSignupView.as_view(), name="embedded-signup"),

    path("companies/<int:company_id>/accounts/<int:account_id>/templates/", MessageTemplateView.as_view(), name="template-list-create"),
    path("companies/<int:company_id>/accounts/<int:account_id>/templates/<int:template_id>/", MessageTemplateView.as_view(), name="template-detail"),
    path("companies/<int:company_id>/accounts/<int:account_id>/templates/sync/", MessageTemplateSyncView.as_view(), name="template-sync"),

    path("companies/<int:company_id>/branches/<int:branch_id>/numbers/", WhatsAppNumberView.as_view(), name="number-list-create"),
    path("companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/", WhatsAppNumberView.as_view(), name="number-detail"),
    path("companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/validate/", WhatsAppNumberValidationView.as_view(), name="number-validate"),
    path("companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/monitoring/", WhatsAppMonitoringView.as_view(), name="number-monitoring"),

    path("companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/templates/", AvailableMessageTemplateView.as_view(), name="available-template-list"),
    path("companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/templates/send/", NewConversationTemplateSendView.as_view(), name="template-new-conversation-send"),

    path("companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/assignments/", NumberAssignmentView.as_view(), name="assignment-list-create"),
    path("companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/assignments/<int:assignment_id>/", NumberAssignmentView.as_view(), name="assignment-detail"),

    path("monitoring/context/", MonitoringContextView.as_view(), name="monitoring-context"),

    path("companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/conversations/", ConversationView.as_view(), name="conversation-list"),
    path("companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/conversations/<int:conversation_id>/", ConversationView.as_view(), name="conversation-detail"),
    path("companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/conversations/<int:conversation_id>/read/", ConversationReadView.as_view(), name="conversation-read"),

    path("companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/conversations/<int:conversation_id>/messages/", MessageView.as_view(), name="message-list"),
    path("companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/conversations/<int:conversation_id>/messages/send/", OutboundMessageView.as_view(), name="message-send"),
    path("companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/conversations/<int:conversation_id>/messages/<int:message_id>/", MessageView.as_view(), name="message-detail"),

    path("companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/conversations/<int:conversation_id>/templates/send/", ConversationTemplateSendView.as_view(), name="conversation-template-send"),

    path("companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/conversations/<int:conversation_id>/messages/<int:message_id>/attachments/", MediaAttachmentView.as_view(), name="attachment-list"),
    path("companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/conversations/<int:conversation_id>/messages/<int:message_id>/attachments/<int:attachment_id>/", MediaAttachmentView.as_view(), name="attachment-detail"),
    path("companies/<int:company_id>/branches/<int:branch_id>/numbers/<int:number_id>/conversations/<int:conversation_id>/messages/<int:message_id>/attachments/<int:attachment_id>/content/", MediaAttachmentContentView.as_view(), name="attachment-content"),

    path("webhooks/", MetaWebhookView.as_view(), name="meta-webhook"),
]
