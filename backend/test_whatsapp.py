"""Offline channel, formatting and delivery regressions. No real messages."""
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

for key, value in {"OPENAI_API_KEY": "test-key", "PINECONE_API_KEY": "test-key",
                   "PINECONE_HOST": "https://test.svc.example.com", "SUPABASE_URL": "https://test.supabase.co",
                   "SUPABASE_KEY": "test-key", "HUBSPOT_POLLING_ENABLED": "false"}.items():
    os.environ[key] = value

from whatsapp_formatting import format_whatsapp, split_whatsapp, message_length, whatsapp_rich_text
from delivery_store import DeliveryStore
from handoff import requests_human
from handoff_note import build_handoff_note
import hubspot_bot as bot_module
import salomao_agent as agent_module


class FormattingTests(unittest.TestCase):
    def test_markdown_headings_bold_steps_and_links(self):
        raw = "## Configurar evento\n\n1. Abra **Eventos**.\n2. Toque em **Editar**.\n\nFonte: [Ajuda](https://portal.inchurch.com.br/pt-br/evento)"
        out = format_whatsapp(raw)
        self.assertNotIn("##", out)
        self.assertNotIn("**", out)
        self.assertIn("*Configurar evento*", out)
        self.assertIn("1. Abra *Eventos*.\n2. Toque em *Editar*.", out)
        self.assertIn("Fonte: Ajuda\nhttps://portal.inchurch.com.br/pt-br/evento", out)
        self.assertEqual(format_whatsapp(out), out)

    def test_menu_numbers_email_and_url_underscores_are_preserved(self):
        text = "Financeiro > Entradas\nR$ 1.234,56, versão 2.1, prazo 30 dias.\na_b@example.com\nhttps://portal.inchurch.com.br/a_b_c?q=a&b=2"
        self.assertEqual(format_whatsapp(text), text)

    def test_html_and_internal_markers_never_leak(self):
        out = format_whatsapp("<p><strong>Olá</strong><br>Teste</p><script>alert(1)</script>TRANSFERIR_SUPORTE<REQUIRES_ESCALATION>")
        self.assertEqual(out, "*Olá*\nTeste")
        self.assertNotIn("<script>", whatsapp_rich_text("<script>alert(1)</script>"))
        self.assertIn("&lt;script&gt;", whatsapp_rich_text("<script>alert(1)</script>"))

    def test_tables_turn_into_labeled_rows(self):
        out = format_whatsapp("| Campo | Valor |\n| --- | --- |\n| Evento | Culto |\n| Status | Ativo |")
        self.assertIn("*Campo:* Evento\n*Valor:* Culto", out)
        self.assertNotIn("|", out)

    def test_safe_link_and_balanced_parentheses(self):
        url = "https://portal.inchurch.com.br/titulo_(novo)?a=1&b=2"
        self.assertIn(url, format_whatsapp(f"[Artigo]({url})"))
        self.assertNotIn("javascript:", format_whatsapp("[clique](javascript:alert(1))"))

    def test_short_answer_has_no_artificial_sections(self):
        self.assertEqual(format_whatsapp("Qual tela você está usando?"), "Qual tela você está usando?")
        self.assertEqual(split_whatsapp(""), [])

    def test_split_preserves_all_words_and_limit(self):
        text = "\n\n".join(f"{i}. Abra *Eventos* e verifique a configuração." for i in range(1, 120))
        parts = split_whatsapp(text, 256)
        self.assertGreater(len(parts), 1)
        self.assertEqual(" ".join(" ".join(parts).split()), " ".join(format_whatsapp(text).split()))
        self.assertTrue(all(message_length(p) <= 256 for p in parts))
        self.assertTrue(all(p.count("*") % 2 == 0 for p in parts))

    def test_emoji_clusters_survive_split(self):
        family = "👩🏽‍💻"
        parts = split_whatsapp(family * 70, 64)
        self.assertEqual("".join(parts), family * 70)
        self.assertTrue(all(p.replace(family, "") == "" for p in parts))
        self.assertTrue(all(message_length(p) <= 64 for p in parts))

    def test_url_never_split(self):
        url = "https://portal.inchurch.com.br/" + "a" * 100
        parts = split_whatsapp("Introdução " * 20 + url, 180)
        self.assertTrue(any(url in part for part in parts))
        with self.assertRaises(ValueError):
            split_whatsapp(url, 64)

    def test_long_emphasis_is_unwrapped_without_truncation(self):
        parts = split_whatsapp("*" + "palavra " * 60 + "*", 64)
        self.assertEqual(" ".join(parts).split(), ["palavra"] * 60)
        self.assertTrue(all(message_length(p) <= 64 for p in parts))


class HandoffTests(unittest.TestCase):
    def test_explicit_requests(self):
        for text in ["Quero falar com um atendente", "humano", "Me transfira para uma pessoa", "preciso de suporte",
                     "falar com alguém", "suporte", "Queria falar com o suporte N1", "Tem como falar com uma pessoa?",
                     "Me passa pro suporte", "Pode me encaminhar para o atendimento humano?",
                     "Não quero falar com robô, quero um atendente"]:
            self.assertTrue(requests_human(text), text)

    def test_incidental_words_are_not_handoffs(self):
        for text in ["Como cadastrar uma pessoa?", "gestão de pessoas", "A plataforma tem suporte a PIX?",
                     "Não quero falar com atendente", "Não preciso de suporte", "Não me transfira para o suporte",
                     "Não precisa me transferir para o suporte", "pessoa", "Como dar suporte aos membros?"]:
            self.assertFalse(requests_human(text), text)

    def test_explicit_handoff_needs_no_model(self):
        supervisor = object.__new__(agent_module.SalomaoSupervisorAgent)
        result = supervisor.run_pipeline(message="Quero falar com atendente")
        self.assertTrue(result.requires_human_handoff)
        self.assertEqual(result.model_name, "human_handoff")

    def test_escalation_marker_survives_extraction_for_detection(self):
        supervisor = object.__new__(agent_module.SalomaoSupervisorAgent)
        content = supervisor._extract_content(SimpleNamespace(content="<REQUIRES_ESCALATION>"))
        self.assertTrue(supervisor._check_handoff(content, agent_module.heuristic_triage("evento"))[0])

    def test_handoff_note_contains_context_guidance_result_reason_and_sources(self):
        messages = [
            {"is_from_visitor": True, "text": "O cadastro de membro continua apresentando o mesmo erro."},
            {"is_from_visitor": False, "text": "Abra Pessoas > Membros. Fonte: https://portal.inchurch.com.br/membros"},
            {"is_from_visitor": True, "text": "Não funcionou. Quero falar com o suporte."},
        ]
        note = build_handoff_note(thread_id="t", message_id="m", messages=messages,
            reason="Pedido explícito do cliente.", sources=[])
        for heading in ["Problema e contexto", "Orientações já fornecidas", "Resultado até o momento",
                        "Motivo da transferência", "Fontes consultadas"]:
            self.assertIn(heading, note)
        self.assertIn("Não funcionou", note)
        self.assertEqual(note.count("Não funcionou"), 2)
        self.assertIn("portal.inchurch.com.br/membros", note)
        self.assertIn("SALOMAO-", note)
        self.assertNotIn("<script>", build_handoff_note(thread_id="t", message_id="x",
            messages=[{"is_from_visitor": True, "text": "<script>alert(1)</script>"}], reason="teste"))


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "delivery.sqlite3"
        self.store = DeliveryStore(self.path)
        self.agent = MagicMock()
        self.agent.validate_response_scope.return_value = True
        self.agent.process_message.return_value = {"success": True, "response": "## Orientação\n\nAbra **Eventos**.", "answer_status": "answered"}
        self.bot = bot_module.HubSpotSalomaoBot(store=self.store, agent=self.agent)
        self.ticket = {"properties": {"hs_pipeline": bot_module.SALOMAO_PIPELINE,
                        "hs_pipeline_stage": bot_module.SALOMAO_STATUS,
                        "hubspot_owner_id": bot_module.SALOMAO_ACTOR_ID.removeprefix("A-")}}
        self.ticket_mock = patch.object(bot_module, "get_ticket_by_id", return_value=self.ticket).start()
        patch.object(bot_module, "get_thread_messages", return_value=[]).start()
        self.note_mock = patch.object(bot_module, "create_ticket_handoff_note", return_value={"id": "note1"}).start()
        patch("requests.sessions.Session.request", side_effect=AssertionError("Network is forbidden in offline tests")).start()
        self.addCleanup(patch.stopall)

    def message(self, message_id="m1", **kwargs):
        return {"id": message_id, "is_from_visitor": True, "created_at": datetime.now(timezone.utc).isoformat(), "text": "Como configurar evento?", **kwargs}

    def enqueue(self, transfer=False):
        payload = {"parts": ["Parte um", "Parte dois"], "response": "Parte um\n\nParte dois", "transfer_requested": transfer}
        payload.update(scope_policy_version=bot_module.SCOPE_POLICY_VERSION, scope_digest=bot_module.approval_digest(payload["response"], payload["parts"]))
        return self.store.enqueue("t1", "m1", payload)

    def test_partial_failure_retries_only_missing_part_after_restart(self):
        entry = self.enqueue()
        with patch.object(bot_module, "reply_to_visitor", side_effect=[{"id": "sent1"}, bot_module.HubSpotSendRejected()]) as send:
            self.assertFalse(self.bot._deliver(entry, "ticket")["sent"])
            self.assertEqual(send.call_count, 2)
        restarted = bot_module.HubSpotSalomaoBot(DeliveryStore(self.path), self.agent)
        with patch.object(bot_module, "reply_to_visitor", return_value={"id": "sent2"}) as send:
            restarted.process_thread("t1", "ticket")
            send.assert_called_once_with("t1", "Parte dois")
        self.assertEqual(self.store.get("t1", "m1")["complete"], 1)

    def test_transfer_failure_retries_without_resending_or_survey(self):
        entry = self.enqueue(transfer=True)
        with patch.object(bot_module, "reply_to_visitor", return_value={"id": "sent"}) as send, patch.object(bot_module, "transfer_ticket_to_human_support", side_effect=[False, True]) as transfer:
            result = self.bot._deliver(entry, "ticket")
            self.assertFalse(result["transferred"])
            self.assertEqual(send.call_count, 2)
            result = self.bot._deliver(self.store.get("t1", "m1"), "ticket")
            self.assertTrue(result["transferred"])
            self.assertEqual(send.call_count, 2)
            self.assertEqual(transfer.call_count, 2)
            self.note_mock.assert_called_once()

    def test_uncertain_note_is_never_duplicated_and_ticket_is_not_moved(self):
        entry = self.enqueue(transfer=True)
        self.note_mock.side_effect = TimeoutError
        with patch.object(bot_module, "reply_to_visitor", return_value={"id": "sent"}) as send, \
             patch.object(bot_module, "transfer_ticket_to_human_support") as transfer:
            first = self.bot._deliver(entry, "ticket")
            second = self.bot._deliver(self.store.get("t1", "m1"), "ticket")
        self.assertTrue(first["needs_review"])
        self.assertTrue(second["needs_review"])
        self.note_mock.assert_called_once()
        transfer.assert_not_called()
        self.assertEqual(send.call_count, 2)

    def test_explicit_note_rejection_retries_note_without_resending_reply(self):
        entry = self.enqueue(transfer=True)
        self.note_mock.side_effect = [bot_module.HubSpotNoteRejected(), {"id": "note2"}]
        with patch.object(bot_module, "reply_to_visitor", return_value={"id": "sent"}) as send, \
             patch.object(bot_module, "transfer_ticket_to_human_support", return_value=True) as transfer:
            first = self.bot._deliver(entry, "ticket")
            second = self.bot._deliver(self.store.get("t1", "m1"), "ticket")
        self.assertEqual(first["error"], "handoff_note_rejected")
        self.assertTrue(second["transferred"])
        self.assertEqual(send.call_count, 2)
        self.assertEqual(self.note_mock.call_count, 2)
        transfer.assert_called_once_with("ticket")

    def test_generation_is_cached_until_delivery(self):
        with patch.object(self.bot, "get_unprocessed_visitor_messages", return_value=[self.message()]), patch.object(bot_module, "reply_to_visitor", return_value=None):
            self.bot.process_thread("t1", "ticket")
            self.bot.process_thread("t1", "ticket")
        self.agent.process_message.assert_called_once()
        self.assertFalse(self.store.get("t1", "m1")["complete"])

    def test_no_shared_transfer_state_between_threads(self):
        first = self.enqueue(transfer=True)
        second = self.store.enqueue("t2", "m1", {"parts": ["Olá"], "response": "Olá", "scope_policy_version": bot_module.SCOPE_POLICY_VERSION,
            "scope_digest": bot_module.approval_digest("Olá", ["Olá"])})
        with patch.object(bot_module, "reply_to_visitor", return_value={"id": "sent"}), patch.object(bot_module, "transfer_ticket_to_human_support", return_value=True) as transfer:
            self.bot._deliver(second, "ticket2")
            transfer.assert_not_called()
            self.bot._deliver(first, "ticket1")
            transfer.assert_called_once_with("ticket1")

    def test_filters_and_age(self):
        old = self.message("old", created_at=(datetime.now(timezone.utc) - timedelta(hours=3)).isoformat())
        sent = self.message("sent")
        self.store.enqueue("t1", "sent", {"parts": [], "response": ""})
        with patch.object(bot_module, "get_thread_messages", return_value=[]), patch.object(bot_module, "parse_incoming_messages", return_value=[old, self.message(), sent]):
            self.assertEqual([m["id"] for m in self.bot.get_unprocessed_visitor_messages("t1")], ["m1"])
        self.ticket_mock.return_value = {"properties": {}}
        self.assertEqual(self.bot.process_thread("t1", "ticket"), [])
        self.agent.process_message.assert_not_called()

    def test_failed_model_still_returns_an_honest_message(self):
        self.agent.process_message.return_value = {"success": False, "answer_status": "unavailable"}
        result = self.bot.process_message("t1", self.message())
        self.assertIn("Não consegui", result["response"])
        self.assertFalse(result["success"])

    def test_human_request_has_priority_over_external_scope_and_skips_model(self):
        message = self.message(text="Quero falar com o suporte sobre futebol")
        result = self.bot.process_message("t1", message)
        self.assertTrue(result["transfer_requested"])
        self.assertEqual(result["answer_status"], "human_handoff")
        self.assertIn("Suporte N1", result["response"])
        self.agent.process_message.assert_not_called()

    def test_attachment_mime_is_used_without_extension(self):
        with patch.object(self.bot, "_download_attachment_as_base64", return_value="YWJj"):
            self.bot.process_message("t1", self.message(raw={"attachments": [{"url": "https://api.hubapi.com/file", "mimeType": "image/png"}]}))
        self.assertEqual(self.agent.process_message.call_args.kwargs["image_mime_type"], "image/png")
        self.assertEqual(self.agent.process_message.call_args.kwargs["originating_channel"], "whatsapp")

    def test_hubspot_file_with_m4a_extension_is_transcribed_as_audio(self):
        attachment = {"type": "FILE", "name": "mensagem.m4a",
                      "url": "https://47354717.cdnp1.hubspotusercontent-na1.net/file.m4a?signature=hidden"}
        with patch.object(self.bot, "_download_attachment_as_base64", return_value="YWJj"):
            result = self.bot.process_message("t1", self.message(text="", raw={"attachments": [attachment]}))
        kwargs = self.agent.process_message.call_args.kwargs
        self.assertEqual(kwargs["audio_format"], "m4a")
        self.assertEqual(kwargs["audio_base64"], "YWJj")
        self.assertNotIn("anexo", result["response"].lower())

    def test_model_value_error_is_not_misreported_as_bad_attachment(self):
        self.agent.process_message.side_effect = ValueError("model_failure")
        result = self.bot.process_message("t1", self.message())
        self.assertNotIn("anexo", result["response"].lower())
        self.assertEqual(result["answer_status"], "unavailable")

    def test_untrusted_attachment_does_not_receive_credentials(self):
        with patch.object(bot_module.requests, "get") as get:
            with self.assertRaises(ValueError):
                self.bot._download_attachment_as_base64("https://evil.example/file.png")
            get.assert_not_called()

    def test_handoff_stops_processing_more_messages(self):
        self.agent.process_message.return_value = {"success": True, "response": "Transferindo", "transfer_requested": True}
        with patch.object(self.bot, "get_unprocessed_visitor_messages", return_value=[self.message(), self.message("m2")]), patch.object(bot_module, "reply_to_visitor", return_value={"id": "sent"}), patch.object(bot_module, "transfer_ticket_to_human_support", return_value=True):
            result = self.bot.process_thread("t1", "ticket")
        self.assertEqual(len(result), 1)
        self.agent.process_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()
