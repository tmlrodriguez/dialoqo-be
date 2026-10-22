from django.http import Http404
from django.shortcuts import get_object_or_404

from access.registry import ROLE_REGISTRY
from organizations.models import Branch, Company, UserCompanyAccess

from whatsapp.models import (
    Conversation,
    MediaAttachment,
    Message,
    NumberAssignment,
    WhatsAppBusinessAccount,
    WhatsAppMessageTemplate,
    WhatsAppNumber,
)


# Define your tenant-safe resolvers here.

def resolve_user_company_access(user, company_id):
    """
        DOCSTRING: Resolve User Company Access

        Description:
        - Resolve an active company access record belonging to the authenticated user.
        - Establish the tenant boundary used by monitoring operations.

        Notes:
        - Foreign companies are intentionally exposed as not found.
        - The company must remain active.
        - The user-company relationship must remain active.
    """

    return get_object_or_404(
        UserCompanyAccess.objects.select_related("company"),
        user=user,
        company_id=company_id,
        is_active=True,
        company__is_active=True,
    )



def resolve_monitoring_whatsapp_number(user, company_id, branch_id, number_id):
    """
        DOCSTRING: Resolve Monitoring WhatsApp Number

        Description:
        - Resolve an operational WhatsApp number available to an authenticated MONITOR or MEMBER user.
        - Apply role-specific authorization before exposing conversation resources.

        Notes:
        - MONITOR authorization is established through an active UserCompanyAccess record.
        - MEMBER authorization is established through the active NumberAssignment for the requested number.
        - MEMBER users may access only WhatsApp numbers currently assigned to them.
        - The company, branch, WABA, number, connection, and monitoring lifecycle must remain active.
        - Unauthorized and foreign identifiers intentionally behave as nonexistent.
    """

    if user.role == ROLE_REGISTRY.MONITOR:
        company_access = resolve_user_company_access(user=user, company_id=company_id)

        return get_object_or_404(
            WhatsAppNumber.objects.select_related("company", "branch", "whatsapp_business_account", "whatsapp_business_account__company"),
            id=number_id,
            company=company_access.company,
            branch_id=branch_id,
            branch__company=company_access.company,
            branch__is_active=True,
            whatsapp_business_account__company=company_access.company,
            whatsapp_business_account__is_active=True,
            is_active=True,
            is_connected=True,
            is_monitoring_enabled=True,
        )

    if user.role == ROLE_REGISTRY.MEMBER:
        assignment = get_object_or_404(
            NumberAssignment.objects.select_related(
                "member",
                "whatsapp_number",
                "whatsapp_number__company",
                "whatsapp_number__branch",
                "whatsapp_number__whatsapp_business_account",
            ),
            member=user,
            whatsapp_number_id=number_id,
            whatsapp_number__company_id=company_id,
            whatsapp_number__branch_id=branch_id,
            whatsapp_number__company__is_active=True,
            whatsapp_number__branch__is_active=True,
            whatsapp_number__whatsapp_business_account__is_active=True,
            whatsapp_number__is_active=True,
            whatsapp_number__is_connected=True,
            whatsapp_number__is_monitoring_enabled=True,
            is_active=True,
            unassigned_at__isnull=True,
        )

        return assignment.whatsapp_number

    raise Http404

def resolve_administrative_company(user, company_id):
    """
        DOCSTRING: Resolve Administrative Company

        Description:
        - Resolve an active company owned by the authenticated organization administrator.

        Notes:
        - Administrative company ownership follows the existing created_by ownership model.
        - Foreign company identifiers intentionally return not found.
    """

    return get_object_or_404(Company, id=company_id, created_by=user, is_active=True)


def resolve_company_branch(company, branch_id):
    """
        DOCSTRING: Resolve Company Branch

        Description:
        - Resolve an active branch strictly inside the supplied company tenant.

        Notes:
        - Branch identifiers belonging to another company are treated as nonexistent.
    """

    return get_object_or_404(Branch, id=branch_id, company=company, is_active=True)


def resolve_company_whatsapp_business_account(company, account_id):
    """
        DOCSTRING: Resolve Company WhatsApp Business Account

        Description:
        - Resolve an active WhatsApp Business Account strictly inside the supplied company.

        Notes:
        - The account is resolved directly inside the supplied company tenant.
    """

    return get_object_or_404(
        WhatsAppBusinessAccount.objects.select_related("company"),
        id=account_id,
        company=company,
        is_active=True,
    )


def resolve_company_whatsapp_number(company, branch_id, number_id):
    """
        DOCSTRING: Resolve Company WhatsApp Number

        Description:
        - Resolve an active corporate WhatsApp number inside an explicit company and branch boundary.

        Notes:
        - The branch must belong to the company.
        - The WABA must belong to the company.
        - The WABA and branch must belong to the supplied company.
        - Foreign resource identifiers intentionally return not found.
    """

    return get_object_or_404(
        WhatsAppNumber.objects.select_related(
            "company",
            "branch",
            "whatsapp_business_account",
            "whatsapp_business_account__company",
        ),
        id=number_id,
        company=company,
        branch_id=branch_id,
        branch__company=company,
        branch__is_active=True,
        whatsapp_business_account__company=company,
        is_active=True,
    )


def resolve_whatsapp_number_conversation(whatsapp_number, conversation_id):
    """
        DOCSTRING: Resolve WhatsApp Number Conversation

        Description:
        - Resolve an active conversation strictly inside the supplied WhatsApp number boundary.

        Notes:
        - The customer must belong to the same company as the WhatsApp number.
        - Foreign conversation identifiers intentionally return not found.
    """

    return get_object_or_404(
        Conversation.objects.select_related(
            "customer",
            "customer__company",
            "whatsapp_number",
            "whatsapp_number__company",
            "last_message",
            "last_message__context_message",
        ).prefetch_related("last_message__media_attachments"),
        id=conversation_id,
        whatsapp_number=whatsapp_number,
        customer__company=whatsapp_number.company,
        is_active=True,
    )


def resolve_conversation_message(conversation, message_id):
    """
        DOCSTRING: Resolve Conversation Message

        Description:
        - Resolve an active message strictly inside the supplied conversation.

        Notes:
        - Global message ID resolution is intentionally prohibited for external API access.
    """

    return get_object_or_404(
        Message.objects.select_related(
            "conversation",
            "conversation__whatsapp_number",
            "context_message",
        ).prefetch_related("media_attachments"),
        id=message_id,
        conversation=conversation,
        conversation__whatsapp_number=conversation.whatsapp_number,
        is_active=True,
    )


def resolve_message_media_attachment(message, attachment_id):
    """
        DOCSTRING: Resolve Message Media Attachment

        Description:
        - Resolve an active media attachment strictly inside the supplied message.

        Notes:
        - Attachment identifiers belonging to another tenant or message return not found.
    """

    return get_object_or_404(
        MediaAttachment,
        id=attachment_id,
        message=message,
        message__conversation=message.conversation,
        is_active=True,
    )


def resolve_whatsapp_number_assignment(whatsapp_number, assignment_id):
    """
        DOCSTRING: Resolve WhatsApp Number Assignment

        Description:
        - Resolve an assignment strictly inside the supplied WhatsApp number.

        Notes:
        - The assigned MEMBER AccessUser is loaded with the assignment.
        - Historical assignments remain resolvable.
        - Foreign assignment identifiers intentionally return not found.
    """

    return get_object_or_404(
        NumberAssignment.objects.select_related("member"),
        id=assignment_id,
        whatsapp_number=whatsapp_number,
    )


def resolve_waba_message_template(whatsapp_business_account, template_id, active_only=True):
    """
        DOCSTRING: Resolve WABA Message Template

        Description:
        - Resolve a WhatsApp message template strictly inside the supplied WhatsApp Business Account.

        Notes:
        - Templates belonging to another WABA or company are treated as nonexistent.
        - active_only may be disabled for administrative historical access.
    """

    queryset = WhatsAppMessageTemplate.objects.select_related(
        "whatsapp_business_account",
        "whatsapp_business_account__company",
    ).filter(
        id=template_id,
        whatsapp_business_account=whatsapp_business_account,
        whatsapp_business_account__company=whatsapp_business_account.company,
    )

    if active_only:
        queryset = queryset.filter(is_active=True)

    return get_object_or_404(queryset)
