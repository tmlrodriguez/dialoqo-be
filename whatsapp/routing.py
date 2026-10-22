from django.urls import path

from whatsapp.realtime.consumers import WhatsAppMonitorConsumer

# Define your WebSocket URLs here.

websocket_urlpatterns = [
    path("ws/whatsapp/companies/<int:company_id>/numbers/<int:number_id>/", WhatsAppMonitorConsumer.as_asgi(), name="whatsapp-monitor-websocket"),
]
