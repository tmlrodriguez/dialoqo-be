from datetime import timedelta
from unittest.mock import Mock, patch

from celery.exceptions import Retry
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve, reverse
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from access.registry import ROLE_REGISTRY
from organizations.models import Branch, Company
from whatsapp.models import Conversation, Customer, Message, NumberAssignment, WhatsAppBusinessAccount, WhatsAppMessageTemplate, WhatsAppNumber
from whatsapp.operations.webhooks import persist_whatsapp_webhook_payload
from whatsapp.tasks import process_whatsapp_webhook_task


class TemplateSendingTests(TestCase):
    """
        DOCSTRING: Template Sending Tests

        Description:
        - Verify template submission, recipient routing, and asynchronous delivery reconciliation.

        Notes:
        - Meta HTTP calls and event publication are simulated without sending real WhatsApp messages.
        - Early delivery webhooks must survive until their outbound message is persisted.
        - API acceptance must never imply confirmed delivery.
    """

    def setUp(self):
        self.admin = get_user_model().objects.create(username="administrator", role=ROLE_REGISTRY.ADMINISTRATOR)
        self.member = get_user_model().objects.create(username="member", role=ROLE_REGISTRY.MEMBER, created_by=self.admin)
        self.company = Company.objects.create(name="Test company", created_by=self.admin)
        self.branch = Branch.objects.create(company=self.company, name="Principal")
        self.account = WhatsAppBusinessAccount.objects.create(
            company=self.company, meta_waba_id="test-waba", meta_business_id="test-business",
            display_name="Test account", is_connected=True, is_webhook_configured=True,
        )
        self.number = WhatsAppNumber.objects.create(
            company=self.company, branch=self.branch, whatsapp_business_account=self.account,
            phone_number="+50499990000", meta_phone_number_id="test-phone", display_name="Test number",
            is_connected=True, is_monitoring_enabled=True, monitoring_started_at=timezone.now() - timedelta(hours=1),
        )
        NumberAssignment.objects.create(whatsapp_number=self.number, member=self.member)
        self.customer = Customer.objects.create(company=self.company, phone_number="50488880000")
        self.conversation = Conversation.objects.create(whatsapp_number=self.number, customer=self.customer)
        self.template = WhatsAppMessageTemplate.objects.create(
            whatsapp_business_account=self.account, meta_template_id="test-template",
            name="confirmacion_solicitud_soporte", language="es", category="UTILITY", status="APPROVED",
            parameter_format="NAMED", components=[{
                "type": "BODY", "text": "Hola {{nombre_cliente}}, recibimos tu solicitud {{numero_solicitud}}.",
            }],
        )
        self.components = [{"type": "body", "parameters": [
            {"type": "text", "parameter_name": "nombre_cliente", "text": "Ana"},
            {"type": "text", "parameter_name": "numero_solicitud", "text": "SOP-1024"},
        ]}]
        self.meta_message_id = "wamid.template-test"
        self.remote_response = Mock(ok=True, status_code=200)
        self.remote_response.json.return_value = {
            "messages": [{"id": self.meta_message_id, "message_status": "accepted"}],
        }
        self.http = self.enterContext(patch("whatsapp.meta.client.requests.post", return_value=self.remote_response))
        self.token = self.enterContext(patch("whatsapp.meta.client.get_meta_access_token", return_value="test-token"))
        for target in (
            "whatsapp.operations.templates.schedule_audit_event",
            "whatsapp.operations.templates.schedule_message_created_event",
            "whatsapp.operations.templates.schedule_conversation_updated_event",
            "whatsapp.operations.messages.lifecycle.schedule_message_status_changed_event",
        ):
            self.enterContext(patch(target))

    def send(self, new_conversation=False):
        kwargs = {"company_id": self.company.pk, "branch_id": self.branch.pk, "number_id": self.number.pk}
        name = "whatsapp:template-new-conversation-send" if new_conversation else "whatsapp:conversation-template-send"
        payload = {"template_id": self.template.pk, "components": self.components}
        if new_conversation:
            payload["recipient_phone_number"] = "+504 7777-0000"
        else:
            kwargs["conversation_id"] = self.conversation.pk
        url = reverse(name, kwargs=kwargs)
        request = APIRequestFactory().post(url, payload, format="json")
        force_authenticate(request, user=self.member)
        match = resolve(url)
        response = match.func(request, **match.kwargs)
        response.render()
        return response

    def status_payload(self, status, errors=None, phone_id=None):
        status_data = {
            "id": self.meta_message_id, "status": status,
            "timestamp": str(int(timezone.now().timestamp())),
        }
        if errors:
            status_data["errors"] = errors
        return {"object": "whatsapp_business_account", "entry": [{
            "id": self.account.meta_waba_id, "changes": [{"field": "messages", "value": {
                "metadata": {"phone_number_id": phone_id or self.number.meta_phone_number_id},
                "statuses": [status_data],
            }}],
        }]}

    def test_named_parameters_and_recipient_are_sent_to_the_owning_waba(self):
        response = self.send()

        self.assertEqual(response.status_code, 201, response.data)
        self.token.assert_called_once_with(whatsapp_business_account=self.account)
        args, kwargs = self.http.call_args
        self.assertTrue(args[0].endswith(f"/{self.number.meta_phone_number_id}/messages"))
        self.assertEqual(kwargs["json"], {
            "messaging_product": "whatsapp", "recipient_type": "individual", "to": self.customer.phone_number,
            "type": "template", "template": {
                "name": self.template.name, "language": {"code": "es"}, "components": self.components,
            },
        })
        self.assertEqual(Message.objects.get().text_body, "Hola Ana, recibimos tu solicitud SOP-1024.")

    def test_meta_acceptance_is_pending_until_a_status_webhook_arrives(self):
        response = self.send()

        self.assertEqual(response.status_code, 201, response.data)
        message = response.data["data"]["message"]
        self.assertEqual(message["status"], "PENDING")
        self.assertIsNone(message["status_updated_at"])
        self.assertIsNone(message["sent_at"])
        self.assertIsNone(message["delivered_at"])
        self.assertEqual(response.data["data"]["meta"]["message_status"], "accepted")
        self.assertEqual(message["content_data"]["template"]["meta_message_status"], "accepted")

    def test_meta_hold_is_preserved_without_claiming_transmission(self):
        self.remote_response.json.return_value["messages"][0]["message_status"] = "held_for_quality_assessment"

        response = self.send()

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["data"]["message"]["status"], "PENDING")
        self.assertEqual(response.data["data"]["meta"]["message_status"], "held_for_quality_assessment")

    def test_new_conversation_uses_the_same_submission_rules(self):
        response = self.send(new_conversation=True)

        self.assertEqual(response.status_code, 201, response.data)
        message = Message.objects.get()
        self.assertEqual(message.recipient_phone_number, "50477770000")
        self.assertEqual(message.conversation.customer.phone_number, "50477770000")
        self.assertEqual(message.status, "PENDING")
        self.assertIsNone(message.status_updated_at)
        self.assertEqual(response.data["data"]["meta"]["message_status"], "accepted")

    def test_meta_rejection_does_not_create_a_local_message_or_conversation(self):
        self.remote_response.ok = False
        self.remote_response.status_code = 400
        self.remote_response.json.return_value = {"error": {"code": 132000, "message": "Template parameter mismatch"}}
        conversations_before = Conversation.objects.count()
        customers_before = Customer.objects.count()

        response = self.send(new_conversation=True)

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["data"]["meta"]["error_code"], "132000")
        self.assertFalse(Message.objects.exists())
        self.assertEqual(Conversation.objects.count(), conversations_before)
        self.assertEqual(Customer.objects.count(), customers_before)

    def test_meta_response_without_a_message_id_is_not_treated_as_a_send(self):
        self.remote_response.json.return_value = {"success": True}

        response = self.send()

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Message.objects.exists())

    def test_unapproved_template_is_rejected_before_meta_is_called(self):
        self.template.status = "PENDING"
        self.template.save(update_fields=["status"])

        response = self.send()

        self.assertEqual(response.status_code, 400, response.data)
        self.http.assert_not_called()
        self.assertFalse(Message.objects.exists())

    def test_delivery_webhooks_progress_without_duplicates_or_regression(self):
        self.assertEqual(self.send().status_code, 201)
        for status, expected in (("sent", "SENT"), ("delivered", "DELIVERED"), ("read", "READ")):
            with self.subTest(status=status):
                result = process_whatsapp_webhook_task.run(self.status_payload(status))
                self.assertEqual(result["updated"], 1)
                self.assertEqual(Message.objects.get().status, expected)
        result = process_whatsapp_webhook_task.run(self.status_payload("sent"))
        self.assertEqual(result["updated"], 0)
        self.assertEqual(Message.objects.get().status, "READ")
        self.assertEqual(Message.objects.count(), 1)

    def test_failed_webhook_keeps_the_specific_meta_failure_reason(self):
        self.assertEqual(self.send().status_code, 201)
        payload = self.status_payload("failed", errors=[{
            "code": 131026, "message": "Message undeliverable", "error_data": {"details": "Recipient is not available on WhatsApp."},
        }])

        result = process_whatsapp_webhook_task.run(payload)

        self.assertEqual(result["updated"], 1)
        message = Message.objects.get()
        self.assertEqual(message.status, "FAILED")
        self.assertEqual(message.failure_code, "131026")
        self.assertIn("Recipient is not available on WhatsApp.", message.failure_message)

    def test_early_failure_webhook_is_retried_after_message_persistence(self):
        payload = self.status_payload("failed", errors=[{"code": 131026, "message": "Message undeliverable"}])
        with patch.object(process_whatsapp_webhook_task, "retry", side_effect=Retry()) as retry:
            with self.assertRaises(Retry):
                process_whatsapp_webhook_task.run(payload)
        retry.assert_called_once()
        self.assertFalse(Message.objects.exists())

        self.assertEqual(self.send().status_code, 201)
        result = process_whatsapp_webhook_task.run(payload)

        self.assertEqual(result["updated"], 1)
        message = Message.objects.get()
        self.assertEqual(message.status, "FAILED")
        self.assertEqual(message.failure_code, "131026")

    def test_missing_message_retries_are_bounded(self):
        process_whatsapp_webhook_task.push_request(retries=5)
        try:
            with patch.object(process_whatsapp_webhook_task, "retry") as retry:
                result = process_whatsapp_webhook_task.run(self.status_payload("sent"))
        finally:
            process_whatsapp_webhook_task.pop_request()

        retry.assert_not_called()
        self.assertEqual(result["unmatched_statuses"], 1)

    def test_status_for_an_unmanaged_number_is_not_retried(self):
        with patch.object(process_whatsapp_webhook_task, "retry") as retry:
            result = process_whatsapp_webhook_task.run(self.status_payload("sent", phone_id="unknown-number"))

        retry.assert_not_called()
        self.assertEqual(result.get("unmatched_statuses", 0), 0)
        self.assertEqual(result["ignored"], 1)

    def test_duplicate_status_updates_do_not_create_duplicate_messages(self):
        self.assertEqual(self.send().status_code, 201)
        payload = self.status_payload("delivered")

        first = persist_whatsapp_webhook_payload(payload)
        second = persist_whatsapp_webhook_payload(payload)

        self.assertEqual(first["updated"], 1)
        self.assertEqual(second["updated"], 0)
        self.assertEqual(Message.objects.count(), 1)
