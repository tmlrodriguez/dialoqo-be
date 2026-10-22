from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, connections, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import resolve, reverse
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from access.registry import ROLE_REGISTRY
from organizations.models import Branch, Company
from whatsapp.models import NumberAssignment, WhatsAppBusinessAccount, WhatsAppNumber
from whatsapp.operations.assignments import assign_whatsapp_number, unassign_whatsapp_number


class AssignmentFixtures:
    """
        DOCSTRING: Assignment Fixtures

        Description:
        - Prepare members and numbers spanning multiple companies for assignment tests.

        Notes:
        - Realtime and audit scheduling are mocked to isolate assignment persistence.
        - All companies share an administrator so cross-company member conflicts remain eligible otherwise.
    """

    def setUp(self):
        super().setUp()
        self.events = self.enterContext(patch("whatsapp.operations.assignments.schedule_number_assignment_changed_event"))
        self.audit = self.enterContext(patch("whatsapp.operations.assignments.schedule_audit_event"))
        self.admin = get_user_model().objects.create(username="administrator", role=ROLE_REGISTRY.ADMINISTRATOR)
        self.members = [
            get_user_model().objects.create(username=f"member-{index}", role=ROLE_REGISTRY.MEMBER, created_by=self.admin)
            for index in range(2)
        ]
        self.numbers = []
        for index in range(2):
            company = Company.objects.create(name=f"Company {index}", created_by=self.admin)
            branch = Branch.objects.create(company=company, name="Principal")
            account = WhatsAppBusinessAccount.objects.create(
                company=company, meta_waba_id=f"waba-{index}", display_name=f"Account {index}",
            )
            for suffix in range(2):
                self.numbers.append(WhatsAppNumber.objects.create(
                    company=company, branch=branch, whatsapp_business_account=account,
                    phone_number=f"+504999900{index}{suffix}", meta_phone_number_id=f"phone-{index}-{suffix}",
                    display_name=f"Number {index}-{suffix}",
                ))

    def assign(self, number, member):
        return assign_whatsapp_number(whatsapp_number=number, member=member, actor=self.admin)

    def request_assignment(self, number, member):
        url = reverse("whatsapp:assignment-list-create", kwargs={
            "company_id": number.company_id, "branch_id": number.branch_id, "number_id": number.pk,
        })
        request = APIRequestFactory().post(url, {"member": member.pk}, format="json")
        force_authenticate(request, user=self.admin)
        match = resolve(url)
        return match.func(request, **match.kwargs)


class AssignmentTests(AssignmentFixtures, TestCase):
    """
        DOCSTRING: Assignment Tests

        Description:
        - Verify exclusive active assignments through operations, API requests, and direct model writes.

        Notes:
        - Historical assignments must survive rejected assignments and explicit unassignment.
        - Member exclusivity applies across company boundaries.
    """

    def test_available_member_and_number_can_be_assigned(self):
        response = self.request_assignment(self.numbers[0], self.members[0])

        self.assertEqual(response.status_code, 201, response.data)
        assignment = NumberAssignment.objects.get()
        self.assertEqual(assignment.member_id, self.members[0].pk)
        self.assertEqual(assignment.whatsapp_number_id, self.numbers[0].pk)
        self.assertTrue(assignment.is_active)
        self.assertIsNone(assignment.unassigned_at)
        self.events.assert_called_once()
        self.audit.assert_called_once()

    def test_member_cannot_receive_a_second_number(self):
        existing = NumberAssignment.objects.create(whatsapp_number=self.numbers[0], member=self.members[0])

        with self.assertRaises(ValidationError) as caught:
            self.assign(self.numbers[1], self.members[0])

        self.assertIn("member", caught.exception.message_dict)
        existing.refresh_from_db()
        self.assertTrue(existing.is_active)
        self.assertIsNone(existing.unassigned_at)
        self.assertEqual(NumberAssignment.objects.count(), 1)
        self.events.assert_not_called()
        self.audit.assert_not_called()

    def test_occupied_number_cannot_be_transferred_implicitly(self):
        existing = NumberAssignment.objects.create(whatsapp_number=self.numbers[0], member=self.members[0])
        original_updated_at = existing.updated_at

        with self.assertRaises(ValidationError) as caught:
            self.assign(self.numbers[0], self.members[1])

        self.assertIn("whatsapp_number", caught.exception.message_dict)
        existing.refresh_from_db()
        self.assertTrue(existing.is_active)
        self.assertIsNone(existing.unassigned_at)
        self.assertEqual(existing.updated_at, original_updated_at)
        self.assertEqual(existing.member_id, self.members[0].pk)
        self.assertEqual(NumberAssignment.objects.count(), 1)
        self.events.assert_not_called()
        self.audit.assert_not_called()

    def test_duplicate_assignment_of_the_same_pair_is_rejected(self):
        NumberAssignment.objects.create(whatsapp_number=self.numbers[0], member=self.members[0])

        with self.assertRaises(ValidationError):
            self.assign(self.numbers[0], self.members[0])

        self.assertEqual(NumberAssignment.objects.count(), 1)

    def test_member_exclusivity_applies_across_companies(self):
        NumberAssignment.objects.create(whatsapp_number=self.numbers[0], member=self.members[0])
        self.assertNotEqual(self.numbers[0].company_id, self.numbers[2].company_id)

        with self.assertRaises(ValidationError) as caught:
            self.assign(self.numbers[2], self.members[0])

        self.assertIn("member", caught.exception.message_dict)
        self.assertEqual(NumberAssignment.objects.count(), 1)

    def test_api_returns_400_for_a_member_with_an_active_number(self):
        NumberAssignment.objects.create(whatsapp_number=self.numbers[0], member=self.members[0])

        response = self.request_assignment(self.numbers[1], self.members[0])

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("member", response.data["data"])
        self.assertEqual(NumberAssignment.objects.count(), 1)

    def test_api_returns_400_for_an_occupied_number(self):
        NumberAssignment.objects.create(whatsapp_number=self.numbers[0], member=self.members[0])

        response = self.request_assignment(self.numbers[0], self.members[1])

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("whatsapp_number", response.data["data"])
        self.assertEqual(NumberAssignment.objects.count(), 1)

    def test_explicit_unassignment_releases_both_sides_and_preserves_history(self):
        existing = self.assign(self.numbers[0], self.members[0])

        closed = unassign_whatsapp_number(whatsapp_number=self.numbers[0], actor=self.admin)
        replacement_number = self.assign(self.numbers[1], self.members[0])
        replacement_member = self.assign(self.numbers[0], self.members[1])

        existing.refresh_from_db()
        self.assertEqual(closed.pk, existing.pk)
        self.assertFalse(existing.is_active)
        self.assertIsNotNone(existing.unassigned_at)
        self.assertEqual(existing.member_id, self.members[0].pk)
        self.assertEqual(existing.whatsapp_number_id, self.numbers[0].pk)
        self.assertTrue(replacement_number.is_active)
        self.assertTrue(replacement_member.is_active)
        self.assertEqual(NumberAssignment.objects.count(), 3)
        self.assertEqual(NumberAssignment.objects.filter(is_active=True).count(), 2)

    def test_inactive_assignment_history_does_not_block_a_new_assignment(self):
        closed_at = timezone.now()
        for _ in range(2):
            NumberAssignment.objects.create(
                whatsapp_number=self.numbers[0], member=self.members[0],
                is_active=False, assigned_at=closed_at, unassigned_at=closed_at,
            )

        assignment = self.assign(self.numbers[0], self.members[0])

        self.assertTrue(assignment.is_active)
        self.assertEqual(NumberAssignment.objects.count(), 3)

    def test_database_rejects_two_active_numbers_for_a_member(self):
        NumberAssignment.objects.create(whatsapp_number=self.numbers[0], member=self.members[0])

        with self.assertRaises(IntegrityError), transaction.atomic():
            NumberAssignment.objects.create(whatsapp_number=self.numbers[2], member=self.members[0])

        self.assertEqual(NumberAssignment.objects.count(), 1)

    def test_database_rejects_two_active_members_for_a_number(self):
        NumberAssignment.objects.create(whatsapp_number=self.numbers[0], member=self.members[0])

        with self.assertRaises(IntegrityError), transaction.atomic():
            NumberAssignment.objects.create(whatsapp_number=self.numbers[0], member=self.members[1])

        self.assertEqual(NumberAssignment.objects.count(), 1)

    def test_model_validation_rejects_a_member_with_another_active_assignment(self):
        NumberAssignment.objects.create(whatsapp_number=self.numbers[0], member=self.members[0])

        with self.assertRaises(ValidationError):
            NumberAssignment(whatsapp_number=self.numbers[1], member=self.members[0]).full_clean()

    def test_member_eligibility_rules_are_preserved(self):
        foreign_admin = get_user_model().objects.create(username="foreign-administrator", role=ROLE_REGISTRY.ADMINISTRATOR)
        invalid_members = [
            get_user_model().objects.create(username="monitor", role=ROLE_REGISTRY.MONITOR, created_by=self.admin),
            get_user_model().objects.create(username="inactive", role=ROLE_REGISTRY.MEMBER, created_by=self.admin, is_active=False),
            get_user_model().objects.create(username="foreign-member", role=ROLE_REGISTRY.MEMBER, created_by=foreign_admin),
        ]

        for member in invalid_members:
            with self.subTest(member=member.username), self.assertRaises(ValidationError):
                self.assign(self.numbers[0], member)

        self.assertFalse(NumberAssignment.objects.exists())

    def test_operation_rechecks_current_member_state(self):
        get_user_model().objects.filter(pk=self.members[0].pk).update(is_active=False)

        with self.assertRaises(ValidationError):
            self.assign(self.numbers[0], self.members[0])

        self.assertFalse(NumberAssignment.objects.exists())


@skipUnless(connection.vendor == "postgresql", "Assignment concurrency requires PostgreSQL row locks.")
class AssignmentConcurrencyTests(AssignmentFixtures, TransactionTestCase):
    """
        DOCSTRING: Assignment Concurrency Tests

        Description:
        - Verify conflicting simultaneous assignments resolve to one success and one validation error.

        Notes:
        - Each worker uses its own PostgreSQL connection and transaction.
        - Database timeouts bound waits and surface deadlocks as test failures.
    """

    def assign_in_connection(self, number_id, member_id, barrier):
        """
            DOCSTRING: Assign In Connection

            Description:
            - Attempt an assignment from an independently synchronized database connection.

            Notes:
            - Worker connections are closed even when assignment fails.
            - Unexpected database failures propagate to the calling test.
        """

        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SET lock_timeout = '3000ms'")
                cursor.execute("SET statement_timeout = '5000ms'")
            number = WhatsAppNumber.objects.get(pk=number_id)
            member = get_user_model().objects.get(pk=member_id)
            actor = get_user_model().objects.get(pk=self.admin.pk)
            barrier.wait(timeout=5)
            try:
                assignment = assign_whatsapp_number(whatsapp_number=number, member=member, actor=actor)
            except ValidationError as error:
                return "rejected", error.message_dict
            return "assigned", assignment.pk
        finally:
            connections.close_all()

    def competing_assignments(self, pairs):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self.assign_in_connection, number.pk, member.pk, barrier)
                for number, member in pairs
            ]
            return [future.result(timeout=10) for future in futures]

    def test_simultaneous_assignments_of_different_numbers_to_one_member(self):
        results = self.competing_assignments([
            (self.numbers[0], self.members[0]),
            (self.numbers[1], self.members[0]),
        ])

        self.assertCountEqual([status for status, _ in results], ["assigned", "rejected"])
        rejected = next(detail for status, detail in results if status == "rejected")
        self.assertIn("member", rejected)
        self.assertEqual(NumberAssignment.objects.filter(is_active=True).count(), 1)
        self.events.assert_called_once()
        self.audit.assert_called_once()

    def test_simultaneous_assignments_of_one_number_to_different_members(self):
        results = self.competing_assignments([
            (self.numbers[0], self.members[0]),
            (self.numbers[0], self.members[1]),
        ])

        self.assertCountEqual([status for status, _ in results], ["assigned", "rejected"])
        rejected = next(detail for status, detail in results if status == "rejected")
        self.assertIn("whatsapp_number", rejected)
        self.assertEqual(NumberAssignment.objects.filter(is_active=True).count(), 1)
        self.events.assert_called_once()
        self.audit.assert_called_once()
