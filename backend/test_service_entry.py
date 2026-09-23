"""Regression for ticket 48654208379: greet once, then wait for a new input."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import hubspot_bot as module
import delivery_reconcile as recovery
from delivery_store import DeliveryStore


class ServiceEntryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "delivery.sqlite3"
        self.store = DeliveryStore(self.path)
        self.agent = MagicMock()
        self.agent.process_message.return_value = {
            "response": "Vamos verificar o cadastro.", "scope_policy_version": module.SCOPE_POLICY_VERSION}
        self.bot = module.HubSpotSalomaoBot(self.store, self.agent, debounce_seconds=0)
        self.now = datetime.now(timezone.utc) - timedelta(seconds=10)
        self.ticket = {"properties": {
            "hs_pipeline": module.SALOMAO_PIPELINE, "hs_pipeline_stage": module.SALOMAO_STATUS,
            "hubspot_owner_id": module.SALOMAO_ACTOR_ID.removeprefix("A-"),
            module.SALOMAO_ENTRY_PROPERTY: (self.now - timedelta(seconds=30)).isoformat()}}
        self.messages = [self.message("hello", -240, "oi"), self.message("menu", -210, "menu", False),
                         self.message("choice", -180, "2")]
        self.read = patch.object(module, "get_thread_messages", side_effect=lambda *a, **kw: self.messages).start()
        patch.object(module, "get_ticket_by_id", side_effect=lambda *a: self.ticket).start()
        self.send = patch.object(module, "reply_to_visitor", side_effect=self.receipt).start()
        self.addCleanup(patch.stopall)

    def message(self, key, offset, text, incoming=True):
        return {"id": key, "createdAt": (self.now + timedelta(seconds=offset)).isoformat(),
                "type": "MESSAGE", "text": text, "direction": "INCOMING" if incoming else "OUTGOING",
                "senders": [{"actorId": "V-client" if incoming else module.SALOMAO_ACTOR_ID}]}

    def receipt(self, thread_id, text):
        return {"id": f"receipt-{self.send.call_count}", "createdAt": self.now.isoformat()}

    def process(self):
        return self.bot.process_thread("thread", "ticket")

    def test_incident_greets_once_without_answering_hello_and_option_two(self):
        self.process()
        self.process()
        self.process()
        self.send.assert_called_once_with("thread", self.bot.ENTRY_GREETING)
        self.agent.process_message.assert_not_called()
        self.assertEqual(self.store.get("thread", "choice")["payload"]["consumed_reason"], "before_entry_greeting")
        self.assertIn("2", [m["text"] for m in self.store.conversation_messages("thread")])

    def test_only_a_message_after_confirmed_greeting_starts_support(self):
        self.process()
        self.messages.append(self.message("question", 1, "Como cadastro um membro?"))
        self.process()
        self.process()
        self.assertEqual(self.send.call_count, 2)
        self.agent.process_message.assert_called_once()
        self.assertEqual(self.agent.process_message.call_args.kwargs["message"], "Como cadastro um membro?")

    def test_restart_and_repeated_webhook_do_not_repeat_greeting(self):
        self.process()
        self.bot = module.HubSpotSalomaoBot(DeliveryStore(self.path), self.agent, debounce_seconds=0)
        self.process()
        self.send.assert_called_once()
        self.agent.process_message.assert_not_called()

    def test_input_during_greeting_is_context_only_including_late_history(self):
        self.process()
        self.process()
        self.messages.append(self.message("late", -1, "2"))
        self.process()
        self.send.assert_called_once()
        self.agent.process_message.assert_not_called()

    def test_uncertain_greeting_blocks_both_resend_and_support(self):
        self.send.return_value = None
        self.send.side_effect = None
        self.process()
        self.messages.append(self.message("question", 1, "Como cadastro um membro?"))
        result = self.process()
        self.send.assert_called_once()
        self.agent.process_message.assert_not_called()
        self.assertEqual(result[0]["error"], "delivery_uncertain")

    def test_rejected_greeting_retries_once_then_waits(self):
        self.send.side_effect = [module.HubSpotSendRejected(), {"id": "ok", "createdAt": self.now.isoformat()}]
        self.process()
        self.process()
        self.process()
        self.assertEqual(self.send.call_count, 2)
        self.agent.process_message.assert_not_called()

    def test_reconciled_uncertain_greeting_waits_for_new_user_message(self):
        self.send.side_effect = None
        self.send.return_value = None
        self.process()
        entry = self.store.pending("thread")[0]
        receipt = self.message("confirmed", 1, self.bot.ENTRY_GREETING, False)
        receipt["createdAt"] = datetime.now(timezone.utc).isoformat()
        receipt["threadId"] = "thread"
        with patch.object(recovery, "fetch_receipt", return_value=receipt):
            recovery.reconcile(self.store, thread_id="thread", message_id=entry["message_id"],
                part=0, remote_id="confirmed", operator="offline-test", reason="confirmed receipt", apply=True)
        self.process()
        self.send.assert_called_once()
        self.agent.process_message.assert_not_called()

    def test_empty_history_still_greets_on_entry(self):
        self.messages = []
        self.process()
        self.process()
        self.send.assert_called_once()

    def test_history_failure_never_sends(self):
        self.read.side_effect = module.HubSpotReadError()
        self.process()
        self.send.assert_not_called()

    def test_invalid_entry_date_does_not_answer_old_menu(self):
        self.ticket["properties"][module.SALOMAO_ENTRY_PROPERTY] = None
        self.assertEqual(self.process()[0]["error"], "entry_timestamp_unavailable")
        self.send.assert_not_called()

    def test_existing_confirmed_service_is_adopted_without_another_greeting(self):
        text = "Olá! Como posso ajudar?"
        self.store.enqueue("thread", "legacy", {"response": text, "parts": [text]})
        self.store.begin_part("thread", "legacy", 0)
        self.store.confirm_delivery_part("thread", "legacy", 0, {"id": "old", "createdAt": self.now.isoformat()}, text)
        self.store.complete("thread", "legacy")
        self.process()
        self.send.assert_not_called()
        self.agent.process_message.assert_not_called()

    def test_reentry_gets_new_greeting_without_replaying_old_inputs(self):
        self.process()
        self.ticket["properties"][module.SALOMAO_ENTRY_PROPERTY] = (self.now + timedelta(seconds=2)).isoformat()
        self.now += timedelta(seconds=3)
        self.process()
        self.process()
        self.assertEqual(self.send.call_count, 2)
        self.agent.process_message.assert_not_called()

    def test_ineligible_ticket_does_not_greet(self):
        self.ticket["properties"]["hs_pipeline_stage"] = "other"
        self.process()
        self.send.assert_not_called()
