"""Meta webhook verification, authentication, and task dispatch."""

import hashlib
import hmac
import json
import secrets

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_400_BAD_REQUEST, HTTP_403_FORBIDDEN
from rest_framework.views import APIView

from whatsapp.meta.auth.credentials import get_meta_app_secret, get_meta_verify_token
from whatsapp.tasks import process_whatsapp_webhook_task


class MetaWebhookView(APIView):
    """
        DOCSTRING: Meta Webhook View

        Description:
        - Provide the single public webhook endpoint used by the Dialoqo Meta application.
        - Authenticate Meta requests synchronously using global platform credentials.
        - Queue validated webhook payloads for asynchronous tenant resolution and persistence.

        Notes:
        - This endpoint does not require Dialoqo authentication.
        - All customer WABAs connected to the Dialoqo Meta application deliver events to this endpoint.
        - GET verification remains synchronous because Meta requires the challenge response.
        - POST signature validation remains synchronous because untrusted payloads must never enter the task broker.
        - Company ownership is resolved later from the globally unique Meta Phone Number ID in the payload.
        - Sensitive Meta credentials are never passed to Celery.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        mode = request.query_params.get("hub.mode")
        supplied_verify_token = request.query_params.get("hub.verify_token")
        challenge = request.query_params.get("hub.challenge")

        if not mode or not supplied_verify_token or challenge is None:
            response_payload = {
                "error_message": "Verificación de webhook rechazada: los parámetros requeridos no fueron proporcionados.",
                "data": {},
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        try:
            verify_token = get_meta_verify_token()
        except ImproperlyConfigured:
            response_payload = {
                "error_message": "Verificación de webhook rechazada: la configuración global de Meta no se encuentra disponible.",
                "data": {},
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        if mode != "subscribe":
            response_payload = {
                "error_message": "Verificación de webhook rechazada: el modo de verificación no es válido.",
                "data": {},
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        if not secrets.compare_digest(supplied_verify_token, verify_token):
            response_payload = {
                "error_message": "Verificación de webhook rechazada: el token de verificación no es válido.",
                "data": {},
            }

            return Response(response_payload, status=HTTP_403_FORBIDDEN)

        return HttpResponse(challenge, status=HTTP_200_OK, content_type="text/plain")

    def post(self, request):
        try:
            app_secret = get_meta_app_secret()
        except ImproperlyConfigured:
            response_payload = {
                "error_message": "Recepción de webhook rechazada: la configuración global de Meta no se encuentra disponible.",
                "data": {},
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        signature_header = request.headers.get("X-Hub-Signature-256")

        if not signature_header:
            response_payload = {
                "error_message": "Recepción de webhook rechazada: la firma de Meta no fue proporcionada.",
                "data": {},
            }

            return Response(response_payload, status=HTTP_403_FORBIDDEN)

        expected_signature = "sha256=" + hmac.new(
            app_secret.encode("utf-8"),
            request.body,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(signature_header, expected_signature):
            response_payload = {
                "error_message": "Recepción de webhook rechazada: la firma de Meta no es válida.",
                "data": {},
            }

            return Response(response_payload, status=HTTP_403_FORBIDDEN)

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            response_payload = {
                "error_message": "Recepción de webhook rechazada: el contenido recibido no contiene JSON válido.",
                "data": {},
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        try:
            task = process_whatsapp_webhook_task.delay(payload)
        except Exception:
            response_payload = {
                "error_message": "Recepción de webhook rechazada: no fue posible colocar el evento en procesamiento.",
                "data": {},
            }

            return Response(response_payload, status=HTTP_400_BAD_REQUEST)

        response_payload = {
            "success_message": "Webhook de Meta recibido correctamente.",
            "data": {
                "accepted": True,
                "task_id": task.id,
            },
        }

        return Response(response_payload, status=HTTP_200_OK)
