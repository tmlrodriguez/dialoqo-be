"""Pagination for conversations, messages, and message templates."""

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class ConversationPagination(PageNumberPagination):
    """
        DOCSTRING: Conversation Pagination

        Description:
        - Provide page-number pagination for monitored WhatsApp conversation lists.
        - Return pagination metadata required by the Dialoqo frontend.

        Notes:
        - The default page size is optimized for conversation-list rendering.
        - Clients may request a smaller or larger page through page_size.
        - page_size is capped to prevent excessively large conversation queries.
    """
    page_size = 30
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response({"success_message": "Conversaciones extraídas correctamente.", "data": {"count": self.page.paginator.count, "next": self.get_next_link(), "previous": self.get_previous_link(), "results": data}})


class MessagePagination(PageNumberPagination):
    """
        DOCSTRING: Message Pagination

        Description:
        - Provide page-number pagination for monitored WhatsApp message history.
        - Return pagination metadata required by the Dialoqo conversation viewer.

        Notes:
        - Messages are returned chronologically inside each requested page.
        - The default page size supports efficient incremental history loading.
        - page_size is capped to protect the message-history endpoint from excessively large queries.
    """

    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200

    def get_paginated_response(self, data):
        return Response({"success_message": "Mensajes extraídos correctamente.", "data": {"count": self.page.paginator.count, "next": self.get_next_link(), "previous": self.get_previous_link(), "results": data}})


class MessageTemplatePagination(PageNumberPagination):
    """
        DOCSTRING: Message Template Pagination

        Description:
        - Provide page-number pagination for WhatsApp message-template catalogs.

        Notes:
        - Clients may customize page size within the configured maximum.
        - Pagination protects the template catalog endpoint from excessively large responses.
    """

    page_size = 30
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response(
            {
                "success_message": (
                    "Plantillas de WhatsApp extraídas correctamente."
                ),
                "data": {
                    "count": self.page.paginator.count,
                    "next": self.get_next_link(),
                    "previous": self.get_previous_link(),
                    "results": data,
                },
            }
        )
