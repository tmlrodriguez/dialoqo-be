from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction

from whatsapp.models import (
    Conversation,
    ConversationReadState,
    Message,
    NumberAssignment,
    WhatsAppNumber,
)
from whatsapp.registry import REALTIME_EVENT_REGISTRY
from whatsapp.serializers.business import MessageSerializer
from whatsapp.serializers.snapshots import (
    ConversationReadStateSnapshotSerializer,
    ConversationSnapshotSerializer,
    NumberAssignmentSnapshotSerializer,
)

# Define your realtime helpers here.

def get_whatsapp_number_group_name(company_id, whatsapp_number_id):
    """
        DOCSTRING: Get WhatsApp Number Group Name

        Description:
        - Build the Channels group name used for realtime events belonging to one company-owned WhatsApp number.

        Notes:
        - Company and WhatsApp number identifiers are included to preserve tenant isolation.
        - All monitors connected to the same authorized WhatsApp number join this group.
        - Group names must remain deterministic so HTTP operations and WebSocket consumers resolve the same destination.
    """

    return f"wa.number.{company_id}.{whatsapp_number_id}"


def get_whatsapp_user_group_name(user_id, company_id, whatsapp_number_id):
    """
        DOCSTRING: Get WhatsApp User Group Name

        Description:
        - Build the private Channels group name used for monitor-specific realtime events.

        Notes:
        - Read-state changes belong to one authenticated user and must not be broadcast as another user's state.
        - The company and WhatsApp number identifiers preserve tenant and resource isolation.
    """

    return f"wa.user.{user_id}.{company_id}.{whatsapp_number_id}"


def send_realtime_group_event(group_name, event_name, data):
    """
        DOCSTRING: Send Realtime Group Event

        Description:
        - Publish a normalized Dialoqo realtime event to a Channels group.

        Notes:
        - Database operations should normally call this helper through transaction.on_commit.
        - The transport type realtime.event is dispatched to realtime_event on the WebSocket consumer.
        - Business event identity remains inside the event field.
    """

    channel_layer = get_channel_layer()

    if channel_layer is None:
        return

    async_to_sync(channel_layer.group_send)(
        group_name,
        {
            "type": "realtime.event",
            "event": event_name,
            "data": data,
        },
    )


def schedule_message_created_event(message):
    """
        DOCSTRING: Schedule Message Created Event

        Description:
        - Publish a newly persisted WhatsApp message after the surrounding database transaction commits successfully.

        Notes:
        - The message is reloaded after commit so clients receive committed state only.
        - The owning WhatsApp number determines the realtime group.
        - A corresponding conversation-updated event is published separately.
    """

    message_id = message.id

    def publish():
        persisted_message = Message.objects.select_related(
            "conversation",
            "conversation__whatsapp_number",
            "context_message",
        ).prefetch_related("media_attachments").filter(id=message_id, is_active=True).first()

        if persisted_message is None:
            return

        whatsapp_number = persisted_message.conversation.whatsapp_number
        group_name = get_whatsapp_number_group_name(company_id=whatsapp_number.company_id, whatsapp_number_id=whatsapp_number.id)

        send_realtime_group_event(
            group_name=group_name,
            event_name=REALTIME_EVENT_REGISTRY.MESSAGE_CREATED,
            data={
                "conversation_id": persisted_message.conversation_id,
                "message": MessageSerializer(persisted_message).data,
            },
        )

    transaction.on_commit(publish)


def schedule_message_updated_event(message):
    """
        DOCSTRING: Schedule Message Updated Event

        Description:
        - Publish the latest visible representation of an edited or revoked WhatsApp message after transaction commit.

        Notes:
        - Revoked message serializers automatically hide original content.
        - The event updates an existing frontend message rather than creating another message.
    """

    message_id = message.id

    def publish():
        persisted_message = Message.objects.select_related(
            "conversation",
            "conversation__whatsapp_number",
            "context_message",
        ).prefetch_related("media_attachments").filter(id=message_id, is_active=True).first()

        if persisted_message is None:
            return

        whatsapp_number = persisted_message.conversation.whatsapp_number
        group_name = get_whatsapp_number_group_name(company_id=whatsapp_number.company_id, whatsapp_number_id=whatsapp_number.id)

        send_realtime_group_event(
            group_name=group_name,
            event_name=REALTIME_EVENT_REGISTRY.MESSAGE_UPDATED,
            data={
                "conversation_id": persisted_message.conversation_id,
                "message": MessageSerializer(persisted_message).data,
            },
        )

    transaction.on_commit(publish)


def schedule_message_status_changed_event(message):
    """
        DOCSTRING: Schedule Message Status Changed Event

        Description:
        - Publish an outbound WhatsApp delivery lifecycle change after transaction commit.

        Notes:
        - SENT, DELIVERED, READ, and FAILED transitions are delivered through this event.
        - The frontend updates the existing message using its database and Meta message identifiers.
    """

    message_id = message.id

    def publish():
        persisted_message = Message.objects.select_related(
            "conversation",
            "conversation__whatsapp_number",
            "context_message",
        ).prefetch_related("media_attachments").filter(id=message_id, is_active=True).first()

        if persisted_message is None:
            return

        whatsapp_number = persisted_message.conversation.whatsapp_number
        group_name = get_whatsapp_number_group_name(company_id=whatsapp_number.company_id, whatsapp_number_id=whatsapp_number.id)

        send_realtime_group_event(
            group_name=group_name,
            event_name=REALTIME_EVENT_REGISTRY.MESSAGE_STATUS_CHANGED,
            data={
                "conversation_id": persisted_message.conversation_id,
                "message": MessageSerializer(persisted_message).data,
            },
        )

    transaction.on_commit(publish)


def schedule_conversation_updated_event(conversation):
    """
        DOCSTRING: Schedule Conversation Updated Event

        Description:
        - Publish the latest conversation preview after transaction commit.

        Notes:
        - Conversation ordering and latest-message information may change when messages arrive or are sent.
        - User-specific read state is intentionally excluded because it is published through a private user group.
    """

    conversation_id = conversation.id

    def publish():
        persisted_conversation = Conversation.objects.select_related(
            "customer",
            "last_message",
            "last_message__context_message",
            "whatsapp_number",
        ).prefetch_related("last_message__media_attachments").filter(id=conversation_id, is_active=True).first()

        if persisted_conversation is None:
            return

        whatsapp_number = persisted_conversation.whatsapp_number

        current_assignment = NumberAssignment.objects.select_related("member").filter(whatsapp_number=whatsapp_number, is_active=True).first()

        group_name = get_whatsapp_number_group_name(company_id=whatsapp_number.company_id, whatsapp_number_id=whatsapp_number.id)

        send_realtime_group_event(
            group_name=group_name,
            event_name=REALTIME_EVENT_REGISTRY.CONVERSATION_UPDATED,
            data={
                "conversation": ConversationSnapshotSerializer(
                    persisted_conversation,
                    context={"current_assignment": current_assignment},
                ).data,
            },
        )

    transaction.on_commit(publish)


def schedule_conversation_read_state_changed_event(read_state):
    """
        DOCSTRING: Schedule Conversation Read State Changed Event

        Description:
        - Publish a monitor-specific conversation read-state update after transaction commit.

        Notes:
        - The event is sent only to the authenticated user's private realtime group.
        - Another monitoring user's read state is never represented as the current user's state.
    """

    read_state_id = read_state.id

    def publish():
        persisted_read_state = ConversationReadState.objects.select_related(
            "conversation",
            "conversation__whatsapp_number",
            "last_read_message",
            "user",
        ).filter(id=read_state_id).first()

        if persisted_read_state is None:
            return

        whatsapp_number = persisted_read_state.conversation.whatsapp_number
        group_name = get_whatsapp_user_group_name(user_id=persisted_read_state.user_id, company_id=whatsapp_number.company_id, whatsapp_number_id=whatsapp_number.id)

        send_realtime_group_event(
            group_name=group_name,
            event_name=REALTIME_EVENT_REGISTRY.CONVERSATION_READ_STATE_CHANGED,
            data={
                "conversation_id": persisted_read_state.conversation_id,
                "read_state": ConversationReadStateSnapshotSerializer(persisted_read_state).data,
            },
        )

    transaction.on_commit(publish)


def schedule_number_assignment_changed_event(whatsapp_number):
    """
        DOCSTRING: Schedule Number Assignment Changed Event

        Description:
        - Publish the current assignment state of a WhatsApp number after transaction commit.

        Notes:
        - Assignment history remains available through the normal REST API.
        - The realtime payload communicates only the currently active assignment.
        - None is returned when the number becomes unassigned.
    """

    whatsapp_number_id = whatsapp_number.id

    def publish():
        persisted_number = WhatsAppNumber.objects.filter(id=whatsapp_number_id, is_active=True).first()

        if persisted_number is None:
            return

        current_assignment = NumberAssignment.objects.select_related("member").filter(whatsapp_number=persisted_number, is_active=True).first()

        group_name = get_whatsapp_number_group_name(company_id=persisted_number.company_id, whatsapp_number_id=persisted_number.id)

        send_realtime_group_event(
            group_name=group_name,
            event_name=REALTIME_EVENT_REGISTRY.NUMBER_ASSIGNMENT_CHANGED,
            data={
                "whatsapp_number_id": persisted_number.id,
                "current_assignment": NumberAssignmentSnapshotSerializer(current_assignment).data if current_assignment else None,
            },
        )

    transaction.on_commit(publish)
