"""Conversation previews and per-user read state."""

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from access.registry import ROLE_REGISTRY
from organizations.models import UserCompanyAccess

from whatsapp.models import (
    Conversation,
    ConversationReadState,
    Message,
    NumberAssignment,
)
from whatsapp.realtime.events import schedule_conversation_read_state_changed_event
from whatsapp.registry import MESSAGE_DIRECTION_REGISTRY, MESSAGE_TYPE_REGISTRY


def is_message_relevant_for_unread(message):
    """
        DOCSTRING: Is Message Relevant For Unread

        Description:
        - Determine whether a persisted WhatsApp message should increment monitoring-user unread counters.

        Notes:
        - Only inbound customer messages may create unread state.
        - Outbound messages never increment monitor unread counters.
        - Reaction events do not increment unread counters.
        - Revoked messages do not increment unread counters.
        - Unsupported inbound messages remain relevant because monitors must still be made aware that customer activity occurred.
    """

    if message.direction != MESSAGE_DIRECTION_REGISTRY.INBOUND:
        return False

    if message.is_revoked:
        return False

    if message.message_type == MESSAGE_TYPE_REGISTRY.REACTION:
        return False

    return True


@transaction.atomic
def mark_conversation_as_read(conversation, user):
    """
        DOCSTRING: Mark Conversation As Read

        Description:
        - Mark a monitored WhatsApp conversation as read for a specific monitoring user.
        - Reset the authenticated user's unread counter.
        - Preserve the most recent message acknowledged by the monitoring user.

        Notes:
        - Read state belongs independently to each monitoring user.
        - The conversation row is locked to obtain a consistent latest-message reference.
        - The ConversationReadState row is locked independently when it already exists.
        - Nullable message relationships are not included in SELECT FOR UPDATE joins.
    """

    locked_conversation = Conversation.objects.select_for_update().get(
        id=conversation.id
    )

    last_message = locked_conversation.last_message

    read_state = ConversationReadState.objects.select_for_update().filter(
        conversation=locked_conversation,
        user=user,
    ).first()

    if read_state is None:
        read_state = ConversationReadState.objects.create(
            conversation=locked_conversation,
            user=user,
            is_read=True,
            unread_count=0,
            last_read_message=last_message,
            last_opened_at=timezone.now(),
        )

    else:
        read_state.is_read = True
        read_state.unread_count = 0
        read_state.last_read_message = last_message
        read_state.last_opened_at = timezone.now()

        read_state.save(
            update_fields=[
                "is_read",
                "unread_count",
                "last_read_message",
                "last_opened_at",
                "updated_at",
            ]
        )

    return read_state


@transaction.atomic
def mark_conversation_as_unread(conversation, message):
    """
        DOCSTRING: Mark Conversation As Unread

        Description:
        - Mark a conversation as unread for every authorized MONITOR and the currently assigned MEMBER.
        - Increment unread counters atomically for a relevant inbound message.
        - Publish each user's resulting read state after successful transaction commit.

        Notes:
        - MONITOR users are resolved through active company-access assignments.
        - MEMBER users are resolved through the active assignment of the conversation WhatsApp number.
        - Existing unread counters are incremented through database F expressions.
        - Duplicate users are collapsed before read-state updates are applied.
        - Automatic unread-counter changes are not audit events because they are system-generated message-ingestion state.
    """

    if not is_message_relevant_for_unread(message):
        return 0

    locked_conversation = Conversation.objects.select_for_update().select_related(
        "whatsapp_number__company"
    ).get(id=conversation.id)

    monitor_users = [
        company_access.user
        for company_access in UserCompanyAccess.objects.select_related("user").filter(
            company=locked_conversation.whatsapp_number.company,
            is_active=True,
            user__is_active=True,
            user__role=ROLE_REGISTRY.MONITOR,
        )
    ]

    member_users = [
        assignment.member
        for assignment in NumberAssignment.objects.select_related("member").filter(
            whatsapp_number=locked_conversation.whatsapp_number,
            is_active=True,
            unassigned_at__isnull=True,
            member__is_active=True,
            member__role=ROLE_REGISTRY.MEMBER,
        )
    ]

    users = {
        user.id: user
        for user in [*monitor_users, *member_users]
    }.values()

    updated_count = 0

    for user in users:
        affected_rows = ConversationReadState.objects.filter(
            conversation=locked_conversation,
            user=user,
        ).update(
            is_read=False,
            unread_count=F("unread_count") + 1,
        )

        if not affected_rows:
            try:
                ConversationReadState.objects.create(
                    conversation=locked_conversation,
                    user=user,
                    is_read=False,
                    unread_count=1,
                )
            except IntegrityError:
                ConversationReadState.objects.filter(
                    conversation=locked_conversation,
                    user=user,
                ).update(
                    is_read=False,
                    unread_count=F("unread_count") + 1,
                )

        read_state = ConversationReadState.objects.filter(
            conversation=locked_conversation,
            user=user,
        ).first()

        if read_state:
            schedule_conversation_read_state_changed_event(
                read_state=read_state,
            )

        updated_count += 1

    return updated_count


@transaction.atomic
def recalculate_conversation_read_state(conversation, user):
    """
        DOCSTRING: Recalculate Conversation Read State

        Description:
        - Recalculate the unread message counter for one monitoring user using persisted conversation history.
        - Repair a read-state record if its cached unread counter becomes inconsistent.

        Notes:
        - The conversation and read-state record are locked during recalculation.
        - Only relevant inbound messages are counted.
        - Messages at or before last_read_message are considered acknowledged.
        - Reaction messages and revoked messages are excluded from unread calculation.
        - Automatic read-state repair is not treated as a human audit action.
    """

    locked_conversation = Conversation.objects.select_for_update().get(
        id=conversation.id
    )

    read_state = ConversationReadState.objects.select_for_update().filter(
        conversation=locked_conversation,
        user=user,
    ).first()

    unread_messages = Message.objects.filter(
        conversation=locked_conversation,
        direction=MESSAGE_DIRECTION_REGISTRY.INBOUND,
        is_active=True,
        revoked_at__isnull=True,
    ).exclude(
        message_type=MESSAGE_TYPE_REGISTRY.REACTION
    )

    if read_state is None:
        unread_count = unread_messages.count()

        read_state = ConversationReadState.objects.create(
            conversation=locked_conversation,
            user=user,
            is_read=unread_count == 0,
            unread_count=unread_count,
        )

        return read_state

    if read_state.last_read_message:
        last_read_message = read_state.last_read_message

        unread_messages = unread_messages.filter(
            Q(
                message_timestamp__gt=last_read_message.message_timestamp
            )
            | Q(
                message_timestamp=last_read_message.message_timestamp,
                id__gt=last_read_message.id,
            )
        )

    unread_count = unread_messages.count()

    read_state.unread_count = unread_count
    read_state.is_read = unread_count == 0

    read_state.save(
        update_fields=[
            "unread_count",
            "is_read",
            "updated_at",
        ]
    )

    return read_state


@transaction.atomic
def update_conversation_last_message(conversation, message):
    """
        DOCSTRING: Update Conversation Last Message

        Description:
        - Update the conversation preview only when the supplied message is chronologically equal to or newer than the current latest message.

        Notes:
        - Delayed webhook deliveries must not regress the conversation preview.
        - Edits and revocations modify the existing latest message record directly and therefore do not require chronological replacement.
    """

    locked_conversation = Conversation.objects.select_for_update().get(
        id=conversation.id
    )

    if (
        locked_conversation.last_message_at
        and message.message_timestamp < locked_conversation.last_message_at
    ):
        return locked_conversation

    locked_conversation.last_message = message
    locked_conversation.last_message_at = message.message_timestamp

    locked_conversation.save(
        update_fields=[
            "last_message",
            "last_message_at",
            "updated_at",
        ]
    )

    return locked_conversation
