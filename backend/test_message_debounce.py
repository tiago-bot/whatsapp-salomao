"""Five-second debounce, bounded batches and pre-send refresh, with a fake clock."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import hubspot_bot as module
from delivery_store import DeliveryStore


class DebounceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = DeliveryStore(Path(folder.name) / "delivery.sqlite3")
        self.agent = MagicMock()
        self.agent.validate_response_scope.return_value = True
        self.agent.process_message.return_value = {
            "response": "Orientação consolidada", "success": True,
            "scope_policy_version": module.SCOPE_POLICY_VERSION,
        }
        self.bot = module.HubSpotSalomaoBot(self.store, self.agent, debounce_seconds=5)
        self.base = datetime(2026, 9, 3, 1, 0, tzinfo=timezone.utc)
        self.elapsed = 0.0
        self.sleeps = []
        self.messages = []
        testcase = self

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                value = testcase.base + timedelta(seconds=testcase.elapsed)
                return value if tz else value.replace(tzinfo=None)

        patch.object(module, "datetime", Clock).start()
        patch.object(module.time, "monotonic", side_effect=lambda: self.elapsed).start()
        patch.object(module.time, "sleep", side_effect=self.sleep).start()
        patch.object(module, "get_ticket_by_id", return_value={"properties": {
            "hs_pipeline": module.SALOMAO_PIPELINE, "hs_pipeline_stage": module.SALOMAO_STATUS,
            "hubspot_owner_id": module.SALOMAO_ACTOR_ID.removeprefix("A-")}}).start()
        patch.object(self.bot, "get_unprocessed_visitor_messages", side_effect=self.read_pending).start()
        self.addCleanup(patch.stopall)

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.elapsed += seconds

    def message(self, id, offset, text=None):
        return {"id": id, "created_at": (self.base + timedelta(seconds=offset)).isoformat(),
                "text": text or id, "is_from_visitor": True}

    def read_pending(self, thread_id):
        observed = [m for m in self.messages if datetime.fromisoformat(m["created_at"]) <= self.base + timedelta(seconds=self.elapsed)]
        self.store.remember_messages(thread_id, observed)
        return [m for m in observed if not self.store.get(thread_id, m["id"])]

    def wait(self):
        return self.bot._wait_for_quiet("t", self.read_pending("t"), 20, self.base + timedelta(seconds=20))

    def test_single_message_waits_five_seconds(self):
        self.messages = [self.message("one", 0)]
        result = self.wait()
        self.assertEqual(self.elapsed, 5)
        self.assertEqual([m["id"] for m in result], ["one"])

    def test_every_arrival_resets_pause_without_losing_previous_lines(self):
        self.messages = [self.message("one", 0), self.message("two", 3), self.message("three", 7)]
        result = self.wait()
        self.assertEqual(self.elapsed, 12)
        self.assertEqual([m["id"] for m in result], ["one", "two", "three"])
        self.assertEqual(self.sleeps, [5, 3, 4])

    def test_duplicate_delivery_of_same_webhook_does_not_extend_wait(self):
        message = self.message("one", 0)
        self.messages = [message, message]
        result = self.wait()
        self.assertEqual(self.elapsed, 5)
        self.assertEqual(len(result), 1)

    def test_already_quiet_messages_do_not_wait_an_extra_five_seconds(self):
        self.messages = [self.message("one", 0)]
        self.elapsed = 10
        self.wait()
        self.assertEqual(self.sleeps, [])

    def test_continuous_messages_close_batch_at_twenty_seconds(self):
        self.messages = [self.message(str(offset), offset) for offset in range(0, 29, 4)]
        result = self.wait()
        self.assertEqual(self.elapsed, 20)
        self.assertEqual([m["id"] for m in result], ["0", "4", "8", "12", "16", "20"])

    def test_generation_waits_then_all_inputs_share_one_delivery(self):
        self.messages = [self.message("one", 0, "Quero cadastrar um membro"),
                         self.message("two", 3, "quais dados preciso?")]
        generation_times = []
        def generate(**kwargs):
            generation_times.append(self.elapsed)
            return {"response": "Orientação consolidada", "scope_policy_version": module.SCOPE_POLICY_VERSION}
        self.agent.process_message.side_effect = generate
        with patch.object(module, "reply_to_visitor", return_value={"id": "receipt"}) as send:
            self.bot.process_thread("t", "ticket")
            self.bot.process_thread("t", "ticket")
        self.assertEqual(generation_times, [8])
        send.assert_called_once()
        self.assertEqual(self.store.get("t", "one")["payload"]["coalesced_into"], "two")
        self.assertEqual(self.store.get("t", "two")["payload"]["source_message_ids"], ["one", "two"])
        self.assertTrue(self.store.get("t", "two")["complete"])

    def test_complement_during_generation_replaces_unsent_draft(self):
        self.messages = [self.message("one", 0, "Como cadastro um membro?")]
        questions = []
        def generate(**kwargs):
            questions.append(kwargs["message"])
            if len(questions) == 1:
                self.elapsed = 7
                self.messages.append(self.message("two", 6, "Sem data de nascimento, pode?"))
                return {"response": "RASCUNHO ANTIGO", "scope_policy_version": module.SCOPE_POLICY_VERSION}
            self.assertGreaterEqual(self.elapsed, 11)
            return {"response": "RESPOSTA ATUALIZADA", "scope_policy_version": module.SCOPE_POLICY_VERSION}
        self.agent.process_message.side_effect = generate
        with patch.object(module, "reply_to_visitor", return_value={"id": "receipt"}) as send:
            self.bot.process_thread("t", "ticket")
        self.assertEqual(len(questions), 2)
        self.assertIn("data de nascimento", questions[1])
        send.assert_called_once_with("t", "RESPOSTA ATUALIZADA")
        self.assertTrue(self.store.get("t", "two")["complete"])
        self.assertEqual(self.store.get("t", "one")["payload"]["coalesced_into"], "two")

    def test_later_messages_cannot_extend_closed_batch_indefinitely(self):
        self.messages = [self.message("one", 0)]
        def generate(**kwargs):
            if self.agent.process_message.call_count == 1:
                self.elapsed = 23
                self.messages.append(self.message("next-batch", 22))
            return {"response": kwargs["message"], "scope_policy_version": module.SCOPE_POLICY_VERSION}
        self.agent.process_message.side_effect = generate
        with patch.object(module, "reply_to_visitor", side_effect=[{"id": "receipt-one"}, {"id": "receipt-two"}]) as send:
            self.bot.process_thread("t", "ticket")
        self.assertEqual([call.args[1] for call in send.call_args_list], ["one", "next-batch"])
        self.assertTrue(self.store.get("t", "one")["complete"])
        self.assertTrue(self.store.get("t", "next-batch")["complete"])

    def test_unavailable_presend_check_never_sends_or_marks_inputs_processed(self):
        self.messages = [self.message("one", 0)]
        def generate(**kwargs):
            self.bot.get_unprocessed_visitor_messages.side_effect = module.HubSpotReadError
            return {"response": "Não enviar", "scope_policy_version": module.SCOPE_POLICY_VERSION}
        self.agent.process_message.side_effect = generate
        with patch.object(module, "reply_to_visitor") as send:
            result = self.bot.process_thread("t", "ticket")
        send.assert_not_called()
        self.assertIsNone(self.store.get("t", "one"))
        self.assertEqual(result[0]["error"], "history_unavailable")

    def test_ownership_change_during_wait_stops_generation(self):
        self.messages = [self.message("one", 0)]
        def sleep(seconds):
            self.sleep(seconds)
            module.get_ticket_by_id.return_value = {"properties": {}}
        module.time.sleep.side_effect = sleep
        with patch.object(module, "reply_to_visitor") as send:
            self.bot.process_thread("t", "ticket")
        self.agent.process_message.assert_not_called()
        send.assert_not_called()
