from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from rest_framework.authtoken.models import Token

# Define your WebSocket authentication middleware here.

@database_sync_to_async
def get_user_from_websocket_token(token_key):
    """
        DOCSTRING: Get User From WebSocket Token

        Description:
        - Resolve an active Dialoqo user from a DRF authentication token supplied during WebSocket connection.

        Notes:
        - Invalid, missing, or inactive-user tokens return AnonymousUser.
        - Token values must never be logged.
    """

    if not token_key:
        return AnonymousUser()

    token = Token.objects.select_related("user").filter(key=token_key, user__is_active=True).first()

    if token is None:
        return AnonymousUser()

    return token.user


class TokenQueryAuthMiddleware:
    """
        DOCSTRING: Token Query Authentication Middleware

        Description:
        - Authenticate Dialoqo WebSocket connections using an existing DRF token.

        Notes:
        - The token is currently supplied through the WebSocket query string as token.
        - Production connections must use WSS.
        - A short-lived WebSocket ticket may replace query-string tokens during production hardening.
        - Sensitive token values must never be written to application logs.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        query_string = scope.get("query_string", b"").decode("utf-8")
        query_params = parse_qs(query_string)
        token_values = query_params.get("token", [])
        token_key = token_values[0] if token_values else ""
        scope["user"] = await get_user_from_websocket_token(token_key)

        return await self.app(scope, receive, send)
