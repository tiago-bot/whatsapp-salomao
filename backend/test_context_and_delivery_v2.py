"""Context, durable receipts and cloud checkpoints: no external network."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import httpx
import hubspot_bot as bot_module
import hubspot_service as service
import salomao_agent as agent_module
from conversation_context import format_history, format_agent_context
from conversation_memory import SupabaseConversationMemory
from delivery_store import DeliveryStore
from published_knowledge import contextual_query, previous_source_urls
from whatsapp_formatting import format_whatsapp

MEMBER = {"id": "member-doc", "title": "Cadastro de Membros pelo +Novo", "category": "Pessoas",
    "content": "No cadastro de membro, preencha nome completo, sexo, data de nascimento, país e endereço.",
    "url": "https://portal.inchurch.com.br/pt-br/cadastro-de-membros-manual"}
HISTORY = [{"role": "user", "content": "como faço um novo membro?"},
    {"role": "assistant", "content": "Acesse Pessoas > +Novo > Membro. Campos obrigatórios: nome completo, sexo, data de nascimento, país e endereço.\nFonte: " + MEMBER["url"]}]


class FollowupTests(unittest.TestCase):
    def test_goal_survives_more_than_thirty_followups(self):
        messages = HISTORY[:1] + [{"role": "assistant", "content": "Qual etapa?"}, {"role": "user", "content": "não consegui"}] * 22
        context, count, truncated = format_agent_context(messages)
        self.assertIn("como faço um novo membro?", context)
        self.assertTrue(truncated)
        self.assertLessEqual(len(context), 24000)
        self.assertLessEqual(count, 31)

    def test_attributes_keep_active_object(self):
        for question in ("quais são os obrigatórios?", "Quais campos preciso preencher?", "e a data de nascimento?",
                         "precisa preencher todos?", "e quais são opcionais?", "e o prazo?"):
            with self.subTest(question=question):
                resolved = contextual_query(question, format_history(HISTORY))
                self.assertIn("membro", resolved)
                self.assertIn(question, resolved)

    def test_attributes_survive_multiple_turns(self):
        history = HISTORY + [{"role": "user", "content": "quais são os obrigatórios?"},
                             {"role": "assistant", "content": "Nome completo e outros campos documentados."}]
        self.assertIn("membro", contextual_query("e quais são opcionais?", format_history(history)))

    def test_source_hint_never_carries_into_new_subject_or_external_question(self):
        for question in ("como faço um estorno?", "quantas libertadores o flamengo tem", "outra dúvida: evento"):
            self.assertEqual(previous_source_urls(question, format_history(HISTORY)), [])

    def test_exact_incident_refreshes_source_and_sends_resolved_query(self):
        supervisor = agent_module.SalomaoSupervisorAgent(session_id="test", user_metadata={"originating_channel": "whatsapp"})
        unrelated = {**MEMBER, "id": "other", "title": "Inscrição de evento", "category": "Eventos", "content": "Evento com ingresso."}
        with patch.object(agent_module.knowledge_base, "search", return_value=[unrelated]), \
             patch.object(agent_module.knowledge_base, "search_referenced", return_value=[MEMBER]) as refresh, \
             patch.object(agent_module, "Agent") as model, patch.object(agent_module, "_answer_model_retry_at", 0):
            model.return_value.run.return_value = SimpleNamespace(content={"answer": MEMBER["content"], "source_ids": [MEMBER["id"]]})
            answer = supervisor.run_pipeline(message="quais são os obrigatórios?", conversation_context=format_history(HISTORY))
            payload = json.loads(model.return_value.run.call_args.args[0])
        self.assertEqual(answer.answer_status, "answered")
        refresh.assert_called_once_with([MEMBER["url"]])
        self.assertEqual([a["id"] for a in payload["articles"]], [MEMBER["id"]])
        self.assertIn("membro", payload["resolved_question"])
        self.assertIn("nome completo", answer.message)

    def test_semantic_resolution_is_passed_to_retrieval(self):
        supervisor = agent_module.SalomaoSupervisorAgent(session_id="test", user_metadata={})
        with patch.object(agent_module.knowledge_base, "search", return_value=[MEMBER]) as search, patch.object(agent_module.knowledge_base, "search_referenced", return_value=[]), patch.object(agent_module, "Agent") as model:
            model.return_value.run.return_value = SimpleNamespace(content={"answer": MEMBER["content"], "source_ids": [MEMBER["id"]]})
            supervisor.run_pipeline(message="Pode ser menor de idade?", conversation_context=format_history(HISTORY),
                resolved_question="Existe idade mínima para cadastrar um membro?")
        self.assertIn("membro", search.call_args.args[0])

    def test_short_steps_are_compact_and_repeated_formatting_is_stable(self):
        text = "## Cadastro\n\nAntes de começar, confira os dados.\n\n1. Abra **Pessoas**.\n\n2. Clique em **+Novo**.\n\n3. Salve.\n\nUma observação."
        result = format_whatsapp(text)
        self.assertIn("1. Abra *Pessoas*.\n2. Clique em *+Novo*.\n3. Salve.", result)
        self.assertIn("Salve.\n\nUma observação", result)
        self.assertEqual(format_whatsapp(result), result)


class DurableDeliveryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "delivery.sqlite3"
        self.store = DeliveryStore(self.path)
        self.bot = bot_module.HubSpotSalomaoBot(self.store, MagicMock())
        self.payload = {"response": "Orientação", "parts": ["Orientação"], "scope_policy_version": bot_module.SCOPE_POLICY_VERSION,
            "scope_digest": bot_module.approval_digest("Orientação", ["Orientação"])}
        self.entry = self.store.enqueue("t", "input", self.payload)

    def test_timeout_is_not_resent_after_restart(self):
        with patch.object(bot_module, "reply_to_visitor", side_effect=TimeoutError) as send:
            self.assertTrue(self.bot._deliver(self.entry, "ticket")["needs_review"])
            restarted = bot_module.HubSpotSalomaoBot(DeliveryStore(self.path), MagicMock())
            self.assertTrue(restarted._deliver(self.entry, "ticket")["needs_review"])
            send.assert_called_once()

    def test_crash_after_intent_before_receipt_is_held(self):
        self.assertEqual(self.store.begin_part("t", "input", 0), "send")
        with patch.object(bot_module, "reply_to_visitor") as send:
            self.assertTrue(self.bot._deliver(self.entry, "ticket")["needs_review"])
            send.assert_not_called()

    def test_repeated_stale_entry_cannot_resend_confirmed_part(self):
        with patch.object(bot_module, "reply_to_visitor", return_value={"id": "remote"}) as send:
            self.bot._deliver(self.entry, "ticket")
            self.bot._deliver(self.entry, "ticket")
            send.assert_called_once()
        self.assertEqual(self.store.conversation_messages("t")[0]["id"], "remote")

    def test_receipt_failure_cannot_repeat_accepted_message(self):
        with patch.object(bot_module, "reply_to_visitor", return_value={"id": "remote"}) as send, \
             patch.object(self.store, "confirm_delivery_part", side_effect=sqlite3.OperationalError):
            with self.assertRaises(sqlite3.OperationalError):
                self.bot._deliver(self.entry, "ticket")
            self.assertTrue(self.bot._deliver(self.entry, "ticket")["needs_review"])
            send.assert_called_once()

    def test_atomic_claim_has_one_winner_across_connections(self):
        def claim(_):
            return DeliveryStore(self.path).begin_part("t", "input", 0)
        with ThreadPoolExecutor(max_workers=8) as executor:
            states = list(executor.map(claim, range(8)))
        self.assertEqual(states.count("send"), 1)

    def test_process_lock_is_shared_and_released(self):
        with self.store.thread_lock("t") as acquired:
            self.assertTrue(acquired)
            with DeliveryStore(self.path).thread_lock("t") as second:
                self.assertFalse(second)
            with self.store.thread_lock("other") as independent:
                self.assertTrue(independent)
        with self.store.thread_lock("t") as acquired:
            self.assertTrue(acquired)

    def test_second_process_cannot_take_live_lock(self):
        code = "import sys; from process_lock import thread_lock\nwith thread_lock(sys.argv[1], 't') as acquired: print(acquired)"
        with self.store.thread_lock("t"):
            child = subprocess.run([sys.executable, "-B", "-c", code, str(self.path)],
                capture_output=True, text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(child.stdout.strip(), "False")

    def test_abrupt_process_exit_releases_lock(self):
        code = "import os,sys; from process_lock import thread_lock\nwith thread_lock(sys.argv[1], 't') as acquired: os._exit(0 if acquired else 2)"
        child = subprocess.run([sys.executable, "-B", "-c", code, str(self.path)],
            capture_output=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(child.returncode, 0)
        with self.store.thread_lock("t") as acquired:
            self.assertTrue(acquired)

    def test_confirmed_rejection_can_retry(self):
        with patch.object(bot_module, "reply_to_visitor", side_effect=[service.HubSpotSendRejected(), {"id": "remote"}]) as send:
            self.assertEqual(self.bot._deliver(self.entry, "ticket")["error"], "delivery_rejected")
            self.assertTrue(self.bot._deliver(self.entry, "ticket")["sent"])
            self.assertEqual(send.call_count, 2)

    def test_existing_unconfirmed_rows_migrate_to_hold(self):
        old_path = self.path.parent / "legacy.sqlite3"
        with sqlite3.connect(old_path) as conn:
            conn.execute("CREATE TABLE deliveries(thread_id TEXT,message_id TEXT,payload TEXT,sent_parts INTEGER DEFAULT 0,complete INTEGER DEFAULT 0,PRIMARY KEY(thread_id,message_id))")
            conn.execute("INSERT INTO deliveries VALUES('t','old',?,0,0)", (json.dumps(self.payload),))
        conn.close()
        migrated = DeliveryStore(old_path)
        self.assertEqual(migrated.begin_part("t", "old", 0), "uncertain")

    def test_coalesced_inputs_have_one_outbox_entry_and_are_not_lost_on_restart(self):
        now = datetime.now(timezone.utc)
        pending = [{"id": "a", "text": "Como cadastro um membro?", "created_at": now.isoformat(), "is_from_visitor": True},
                   {"id": "b", "text": "Quais dados são obrigatórios?", "created_at": (now + timedelta(seconds=1)).isoformat(), "is_from_visitor": True}]
        merged = self.bot._coalesce_pending("t", pending)
        self.assertEqual(len(merged), 1)
        self.assertIn(pending[0]["text"], merged[0]["text"])
        self.store.enqueue("other", "b", {**self.payload, "source_message_ids": merged[0]["source_message_ids"]})
        restored = DeliveryStore(self.path)
        self.assertEqual(len(restored.pending("other")), 1)
        self.assertEqual(restored.get("other", "a")["payload"]["coalesced_into"], "b")


class CloudMemoryTests(unittest.TestCase):
    def setUp(self):
        self.rows = {}
        def handler(request):
            if request.method == "POST":
                row = json.loads(request.content)
                self.rows[row["session_id"]] = row
                return httpx.Response(201)
            key = request.url.params["session_id"].removeprefix("eq.")
            return httpx.Response(200, json=[self.rows[key]] if key in self.rows else [])
        client = httpx.Client(base_url="https://memory.test/rest/v1/", transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        self.memory = SupabaseConversationMemory(client)
        self.messages = [{"id": "m", "text": "Como cadastro membro?", "created_at": "2026-09-02T21:00:00Z", "is_from_visitor": True}]

    def test_checkpoint_is_idempotent_isolated_and_restorable(self):
        self.assertTrue(self.memory.save("t", self.messages))
        self.assertTrue(self.memory.save("t", self.messages))
        self.assertEqual(len(self.rows), 1)
        self.assertEqual(self.memory.load("t")[0]["text"], self.messages[0]["text"])
        self.assertEqual(self.memory.load("other"), [])
        self.assertNotEqual(self.memory.session_id("t"), "hubspot_thread_t")

    def test_invalid_snapshot_is_not_used(self):
        self.memory.save("t", self.messages)
        self.rows[self.memory.session_id("t")]["metadata"]["observed_messages"][0]["text"] = "corrupted"
        self.assertEqual(self.memory.load("t"), [])

    def test_cloud_failure_does_not_replace_local_history_or_retry_every_poll(self):
        client = MagicMock()
        client.get.side_effect = TimeoutError
        memory = SupabaseConversationMemory(client)
        self.assertEqual(memory.load("t"), [])
        self.assertEqual(memory.load("t"), [])
        self.assertFalse(memory.save("t", self.messages))
        client.get.assert_called_once()
        client.post.assert_not_called()

    def test_backend_sends_no_unsent_draft_to_cloud(self):
        with tempfile.TemporaryDirectory() as folder:
            store = DeliveryStore(Path(folder) / "outbox.sqlite3")
            store.remember_messages("t", self.messages)
            store.enqueue("t", "draft", {"response": "UNSENT", "parts": ["UNSENT"]})
            bot = bot_module.HubSpotSalomaoBot(store, MagicMock(), self.memory)
            bot._save_memory("t")
            snapshot = self.memory.load("t")
            self.assertEqual(len(snapshot), 1)
            self.assertNotIn("UNSENT", json.dumps(snapshot))

    def test_cloud_restoration_does_not_replay_inputs_when_volume_was_lost(self):
        with tempfile.TemporaryDirectory() as folder:
            store = DeliveryStore(Path(folder) / "outbox.sqlite3")
            store.restore_memory("t", self.messages)
            self.assertIsNotNone(store.get("t", "m"))
            self.assertIn("requires_review", store.get("t", "m")["payload"]["blocked_reason"])
            self.assertEqual(store.pending("t"), [])


class SendFailureTests(unittest.TestCase):
    def test_4xx_rejection_is_distinct_from_5xx_uncertainty(self):
        for status in (400, 429, 500, 502, 503):
            with self.subTest(status=status), patch.object(service.requests, "post") as post:
                post.return_value.status_code = status
                if status in (400, 429):
                    with self.assertRaises(service.HubSpotSendRejected):
                        service.send_message_to_thread("t", "Olá", "c", "a", "s", [])
                else:
                    self.assertIsNone(service.send_message_to_thread("t", "Olá", "c", "a", "s", []))
                self.assertFalse(post.call_args.kwargs["allow_redirects"])
