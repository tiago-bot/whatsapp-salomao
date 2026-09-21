"""Durable intake, recovery boundaries and audited reconciliation, offline."""
from datetime import datetime, timedelta, timezone
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import hubspot_bot as bot_module
import hubspot_service as service
import delivery_reconcile as recovery
from delivery_store import DeliveryStore
from scope_policy import SCOPE_POLICY_VERSION, approval_digest


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "delivery.sqlite3"
        self.store = DeliveryStore(self.path)
        self.now = datetime.now(timezone.utc)
        self.payload = {"response": "Orientação", "parts": ["Orientação"],
                        "scope_policy_version": SCOPE_POLICY_VERSION,
                        "scope_digest": approval_digest("Orientação", ["Orientação"])}
        self.agent = MagicMock()
        self.agent.process_message.return_value = self.payload
        self.bot = bot_module.HubSpotSalomaoBot(self.store, self.agent, debounce_seconds=0)
        self.ticket = {"properties": {"hs_pipeline": service.SALOMAO_PIPELINE,
            "hs_pipeline_stage": service.SALOMAO_STATUS,
            "hubspot_owner_id": service.SALOMAO_ACTOR_ID.removeprefix("A-")}}
        ownership = patch.object(bot_module, "get_ticket_by_id", return_value=self.ticket)
        ownership.start()
        self.addCleanup(ownership.stop)

    def message(self, id, offset=0, **fields):
        return {"id": id, "text": "Dúvida " + id, "is_from_visitor": True,
                "created_at": (self.now + timedelta(seconds=offset)).isoformat(), **fields}

    def hold(self, payload=None, thread="t", message="input"):
        self.store.enqueue(thread, message, payload or self.payload)
        self.store.begin_part(thread, message, 0)
        self.store.failed_part(thread, message, 0)

    def receipt(self, **fields):
        return {"id": "remote", "type": "MESSAGE", "direction": "OUTGOING", "threadId": "t",
                "text": "Orientação", "senders": [{"actorId": service.SALOMAO_ACTOR_ID}],
                "createdAt": (self.now + timedelta(seconds=1)).isoformat(), **fields}

    def reconcile(self, receipt=None, **fields):
        with patch.object(recovery, "fetch_receipt", return_value=receipt or self.receipt()):
            return recovery.reconcile(self.store, **{"thread_id": "t", "message_id": "input",
                "part": 0, "remote_id": "remote", "operator": "operador", "reason": "incidente 42",
                "apply": True, **fields})

    def audit(self):
        with self.store._connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM delivery_reconciliations")]

    def test_fresh_install_does_not_admit_old_history_or_invalid_timestamps(self):
        observed = [self.message("old", -3600), self.message("new"), self.message("bad", created_at="bad")]
        result = self.store.observe_pending("t", observed, self.now - timedelta(minutes=5), self.now)
        self.assertEqual([m["id"] for m in result], ["new"])
        self.assertEqual(self.store.pending_inputs("other"), [])

    def test_attachment_and_pending_survive_restart_age_and_disappearing_page(self):
        message = self.message("audio", text="", raw={"attachments": [{"url": "https://api.hubapi.com/audio"}]})
        self.store.observe_pending("t", [message], self.now - timedelta(minutes=5), self.now)
        restarted = DeliveryStore(self.path)
        later = self.now + timedelta(hours=2)
        pending = restarted.observe_pending("t", [], restarted.intake_cutoff("t", later), later)
        self.assertEqual(pending, [message])
        restarted.restore_memory("t", [{**message, "text": "Transcrição"}])
        self.assertIsNone(restarted.get("t", "audio"))
        restarted.enqueue("t", "audio", {**self.payload, "source_message_ids": ["audio"]})
        self.assertEqual(restarted.pending_inputs("t"), [])

    def test_checkpoint_recovers_messages_missed_while_worker_was_down(self):
        self.store.observe_pending("t", [], self.now - timedelta(minutes=5), self.now)
        later = self.now + timedelta(hours=2)
        cutoff = DeliveryStore(self.path).intake_cutoff("t", later - timedelta(minutes=5))
        pending = self.store.observe_pending("t", [self.message("ancient", -7200), self.message("waiting", 60)], cutoff, later)
        self.assertEqual([m["id"] for m in pending], ["waiting"])

    def test_legacy_hold_recovers_only_messages_since_outstanding_attempt(self):
        self.hold()
        later = self.now + timedelta(hours=2)
        cutoff = self.store.intake_cutoff("t", later - timedelta(minutes=5))
        pending = self.store.observe_pending("t", [self.message("ancient", -7200), self.message("waiting", 60)], cutoff, later)
        self.assertEqual([m["id"] for m in pending], ["waiting"])

    def test_failed_read_does_not_advance_checkpoint(self):
        self.store.observe_pending("t", [], self.now - timedelta(minutes=5), self.now)
        before = self.store.intake_cutoff("t", self.now + timedelta(hours=1))
        with patch.object(bot_module, "get_thread_messages", side_effect=service.HubSpotReadError):
            with self.assertRaises(service.HubSpotReadError):
                self.bot.get_unprocessed_visitor_messages("t")
        self.assertEqual(self.store.intake_cutoff("t", self.now + timedelta(hours=1)), before)

    def test_reconciling_legacy_hold_before_first_poll_preserves_intake_boundary(self):
        self.hold()
        self.reconcile()
        later = self.now + timedelta(hours=2)
        cutoff = self.store.intake_cutoff("t", later - timedelta(minutes=5))
        pending = self.store.observe_pending("t", [self.message("old", -3600), self.message("waiting", 60)], cutoff, later)
        self.assertEqual([m["id"] for m in pending], ["waiting"])

    def test_held_thread_collects_inputs_and_releases_them_once_after_reconciliation(self):
        self.hold()
        with patch.object(bot_module, "get_ticket_by_id", return_value=self.ticket), \
             patch.object(bot_module, "get_thread_messages", return_value=[]), \
             patch.object(bot_module, "parse_incoming_messages", return_value=[self.message("waiting")]), \
             patch.object(bot_module, "reply_to_visitor") as send:
            result = self.bot.process_thread("t", "ticket")
            self.assertTrue(result[0]["needs_review"])
            self.assertEqual([m["id"] for m in self.store.pending_inputs("t")], ["waiting"])
            send.assert_not_called()
            self.agent.process_message.assert_not_called()
        self.reconcile()
        self.bot = bot_module.HubSpotSalomaoBot(DeliveryStore(self.path), self.agent, debounce_seconds=0)
        with patch.object(bot_module, "datetime", wraps=datetime) as clock, \
             patch.object(bot_module, "get_ticket_by_id", return_value=self.ticket), \
             patch.object(bot_module, "get_thread_messages", return_value=[]), \
             patch.object(bot_module, "reply_to_visitor", return_value={"id": "new-receipt"}) as send:
            clock.now.return_value = self.now + timedelta(hours=1)
            self.bot.process_thread("t", "ticket")
            self.bot.process_thread("t", "ticket")
            send.assert_called_once()
        self.assertTrue(self.store.get("t", "waiting")["complete"])

    def test_coalescing_consumes_all_pending_inputs_atomically(self):
        self.store.observe_pending("t", [self.message("a"), self.message("b", 1)], self.now - timedelta(minutes=5), self.now)
        self.store.enqueue("t", "b", {**self.payload, "source_message_ids": ["a", "b"]})
        self.assertEqual(DeliveryStore(self.path).pending_inputs("t"), [])
        self.assertEqual(self.store.get("t", "a")["payload"]["coalesced_into"], "b")

    def test_preview_is_read_only_then_apply_is_audited_and_idempotent(self):
        self.hold()
        original = self.path.read_bytes()
        self.assertEqual(self.reconcile(apply=False)["status"], "preview")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.audit(), [])
        self.assertEqual(self.reconcile()["status"], "applied")
        self.assertTrue(DeliveryStore(self.path).get("t", "input")["complete"])
        self.assertEqual(self.store.conversation_messages("t")[0]["id"], "remote")
        self.assertEqual(self.reconcile()["status"], "already_reconciled")
        self.assertEqual(len(self.audit()), 1)
        self.assertEqual(self.audit()[0]["operator"], "operador")
        self.assertEqual(self.audit()[0]["previous_state"], "uncertain")

    def test_wrong_receipts_and_missing_audit_identity_never_release(self):
        self.hold()
        for fields in ({"direction": "INCOMING"}, {"text": "outra resposta"}, {"threadId": "other"},
                       {"senders": [{"actorId": "A-other"}]}, {"type": "COMMENT"}, {"id": "wrong"},
                       {"createdAt": (self.now - timedelta(days=1)).isoformat()}, {"createdAt": "bad"},
                       {"status": {"statusType": "FAILED"}}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.reconcile(self.receipt(**fields))
        with self.assertRaises(ValueError):
            self.reconcile(operator=" ")
        self.assertFalse(self.store.get("t", "input")["complete"])
        self.assertEqual(self.audit(), [])

    def test_interrupted_sending_can_reconcile_but_retryable_rejection_cannot(self):
        self.store.enqueue("t", "input", self.payload)
        self.store.begin_part("t", "input", 0)
        self.assertEqual(self.reconcile()["previous_state"], "sending")
        self.store.enqueue("t", "rejected", self.payload)
        self.store.begin_part("t", "rejected", 0)
        self.store.failed_part("t", "rejected", 0, retryable=True)
        with self.assertRaisesRegex(ValueError, "attempt_not_uncertain"):
            self.reconcile(message_id="rejected")

    def test_cli_defaults_to_preview_and_requires_apply_for_mutation(self):
        self.hold()
        args = ["--db", str(self.path), "--thread", "t", "--message", "input", "--part", "1",
                "--remote-id", "remote", "--operator", "operador", "--reason", "incidente 42"]
        with patch.object(recovery, "fetch_receipt", return_value=self.receipt()), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(recovery.main(args), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "preview")
            self.assertFalse(self.store.get("t", "input")["complete"])
            output.seek(0)
            output.truncate()
            self.assertEqual(recovery.main(args + ["--apply"]), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "applied")
        self.assertTrue(self.store.get("t", "input")["complete"])

    def test_cli_wrong_database_never_creates_a_new_database(self):
        missing = self.path.parent / "wrong.sqlite3"
        args = ["--db", str(missing), "--thread", "t", "--message", "input", "--part", "1",
                "--remote-id", "remote", "--operator", "operador", "--reason", "incidente 42", "--apply"]
        with patch.object(recovery, "fetch_receipt") as fetch, redirect_stdout(io.StringIO()):
            self.assertEqual(recovery.main(args), 1)
            fetch.assert_not_called()
        self.assertFalse(missing.exists())

    def test_multipart_reconciliation_resumes_only_remaining_parts(self):
        payload = {**self.payload, "parts": ["Orientação", "Continuação"], "response": "Orientação\nContinuação"}
        payload["scope_digest"] = approval_digest(payload["response"], payload["parts"])
        self.hold(payload)
        self.assertFalse(self.reconcile()["complete"])
        with patch.object(bot_module, "reply_to_visitor", return_value={"id": "second"}) as send:
            self.bot._deliver(self.store.get("t", "input"), "ticket")
        send.assert_called_once_with("t", "Continuação")

    def test_out_of_order_conflicting_reused_or_busy_reconciliation_is_rejected(self):
        self.hold()
        with self.assertRaisesRegex(ValueError, "part_out_of_order"):
            self.reconcile(part=1)
        with self.store.thread_lock("t"), self.assertRaisesRegex(ValueError, "conversation_busy"):
            self.reconcile()
        self.reconcile()
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.reconcile(self.receipt(id="other"), remote_id="other")
        self.hold(message="next")
        with self.assertRaisesRegex(ValueError, "already_used"):
            self.reconcile(message_id="next")

    def test_atomic_rollback_includes_receipt_progress_and_audit(self):
        self.hold()
        with self.store._connect() as conn:
            conn.execute("""CREATE TRIGGER simulate_disk_error BEFORE INSERT ON conversation_messages
                BEGIN SELECT RAISE(ABORT,'simulated write failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.reconcile()
        self.assertEqual(self.audit(), [])
        self.assertEqual(self.store.get("t", "input")["sent_parts"], 0)
        self.assertEqual(self.store.begin_part("t", "input", 0), "uncertain")

    def test_note_reconciliation_does_not_repeat_note_or_skip_transfer(self):
        self.hold({**self.payload, "transfer_requested": True, "handoff_note_body": "<p>Referência única</p>"})
        self.store.confirm_delivery_part("t", "input", 0, self.receipt(id="outbound"), "Orientação")
        self.store.begin_handoff_note("t", "input")
        self.store.failed_handoff_note("t", "input")
        receipt = {"id": "remote", "createdAt": self.now.isoformat(),
            "properties": {"hs_note_body": "<p>Referência única</p>"},
            "associations": {"tickets": {"results": [{"id": "ticket"}]}}}
        with self.assertRaisesRegex(ValueError, "does_not_match_note"):
            self.reconcile(receipt, part=None, ticket_id="wrong")
        self.assertFalse(self.reconcile(receipt, part=None, ticket_id="ticket")["complete"])
        with patch.object(bot_module, "reply_to_visitor") as send, \
             patch.object(bot_module, "create_ticket_handoff_note") as note, \
             patch.object(bot_module, "transfer_ticket_to_human_support", return_value=True) as transfer:
            self.bot._deliver(self.store.get("t", "input"), "ticket")
            send.assert_not_called()
            note.assert_not_called()
            transfer.assert_called_once_with("ticket")


class ReceiptReadTests(unittest.TestCase):
    def test_receipt_lookup_uses_exact_id_get_and_disallows_redirect(self):
        with patch.object(recovery.requests, "get") as get:
            get.return_value.status_code = 200
            get.return_value.json.return_value = {"id": "remote"}
            recovery.fetch_receipt("thread", "remote", "part", None)
        self.assertTrue(get.call_args.args[0].endswith("/threads/thread/messages/remote"))
        self.assertFalse(get.call_args.kwargs["allow_redirects"])

    def test_note_lookup_requires_ticket_thread_association(self):
        with patch.object(recovery, "_read", return_value={"threadAssociations": {"associatedTicketId": "other"}}) as read:
            with self.assertRaisesRegex(ValueError, "ticket_thread_mismatch"):
                recovery.fetch_receipt("thread", "remote", "note", "ticket")
            read.assert_called_once()

    def test_receipt_read_failure_fails_closed(self):
        for status in (301, 404, 500):
            with self.subTest(status=status), patch.object(recovery.requests, "get") as get:
                get.return_value.status_code = status
                with self.assertRaisesRegex(ValueError, "receipt_read_failed"):
                    recovery.fetch_receipt("t", "remote", "part", None)

    def test_history_since_checkpoint_is_not_truncated_to_recent_window(self):
        now = datetime.now(timezone.utc)
        recent = [{"id": str(i), "createdAt": (now + timedelta(seconds=i)).isoformat()} for i in range(101)]
        with patch.object(service.requests, "get") as get:
            get.return_value.status_code = 200
            get.return_value.json.side_effect = [
                {"results": recent[1:], "paging": {"next": {"after": "page2"}}},
                {"results": recent[:1] + [{"id": "older", "createdAt": (now - timedelta(days=1)).isoformat()}],
                 "paging": {"next": {"after": "page3"}}}]
            result = service.get_thread_messages("t", since=now, strict=True)
        self.assertEqual(len(result), 102)
        self.assertEqual(get.call_count, 2)

    def test_history_second_page_failure_is_not_a_successful_partial_scan(self):
        with patch.object(service.requests, "get") as get:
            first = MagicMock(status_code=200)
            first.json.return_value = {"results": [], "paging": {"next": {"after": "next"}}}
            get.side_effect = [first, MagicMock(status_code=500)]
            with self.assertRaises(service.HubSpotReadError):
                service.get_thread_messages("t", since=datetime.now(timezone.utc), strict=True)

    def test_history_page_limit_never_silently_discards_unread_backlog(self):
        with patch.object(service.requests, "get") as get:
            get.return_value.status_code = 200
            get.return_value.json.side_effect = [
                {"results": [], "paging": {"next": {"after": str(i)}}} for i in range(100)]
            with self.assertRaises(service.HubSpotReadError):
                service.get_thread_messages("t", since=datetime.now(timezone.utc), strict=True)
            self.assertEqual(get.call_count, 100)


if __name__ == "__main__":
    unittest.main()
