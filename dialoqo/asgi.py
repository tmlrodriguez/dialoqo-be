import os
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application


os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "dialoqo.settings",
)

django_asgi_application = (
    get_asgi_application()
)

from whatsapp.routing import websocket_urlpatterns
from whatsapp.realtime.auth import TokenQueryAuthMiddleware

application = ProtocolTypeRouter(
    {
        "http": django_asgi_application,

        "websocket": TokenQueryAuthMiddleware(
            URLRouter(
                websocket_urlpatterns
            )
        ),
    }
)