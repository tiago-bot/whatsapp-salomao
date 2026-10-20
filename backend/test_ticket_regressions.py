"""Regressions from the WhatsApp conversations reviewed on 2026-10-20.

Tickets 49439291552, 49579702189, 49051141666, 49160069751, 49243865957,
49450247780, 49545550850, 49546361109, 49574262093 and 49210551153. Every
customer text below is quoted from those conversations.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import hubspot_bot as module
import salomao_agent as agent_module
from conversation_context import history_before
from delivery_store import DeliveryStore
from handoff_note import build_handoff_note
from intent_routing import route_intent
from scope_policy import SCOPE_CLARIFY


MENU = ("Olá! Seja muito bem-vindo(a) à inChurch! 💚 Como podemos te ajudar hoje? 🙏 Por favor, escolha "
        "uma das opções abaixo: 1️⃣ Suporte para a plataforma 2️⃣ Dúvidas sobre a plataforma 3️⃣ 2ª via de "
        "boleto Digite apenas o número da opção desejada (1, 2 ou 3).")


class IntentRoutingTests(unittest.TestCase):
    def assertRoute(self, expected, message, recent=(), spoke=True):
        routed = route_intent(message, recent, assistant_spoke=spoke)
        self.assertEqual(routed.intent if routed else None, expected, message)

    def test_contract_requests_go_to_the_team_not_to_event_tutorials(self):
        self.assertRoute("contract", "Preciso saber como faço para cancelar ou suspender o contrato")
        self.assertRoute("contract", "a minha necessidade é cancelamento da assinatura do plano")
        self.assertRoute("contract", "Meu plano tem fidelidade caso eu não queira continuar na inchurch?")
        self.assertRoute("contract", "Solicitei o cancelamento da plataforma, me informaram que em 48 já "
                         "receberia uma mensagem via watts pra confirmar o cancelamento, mas não recebi nada\n"
                         "Recebemos o seu pedido de cancelamento. Segue o número do protocolo do cancelamento: 49404473653.")

    def test_sales_requests_go_to_the_commercial_team(self):
        self.assertRoute("commercial", "Quero assinar a plataforma para minha igreja")
        self.assertRoute("commercial", "Gostaria de conhecer os planos e os valor da plataforma para implantação na minha igreja.")
        self.assertRoute("commercial", "Qual é o valor?", ["Gostaria de saber como funciona", "Tudo",
                         "Eu não conheço nada sobre a plataforma, gostaria de conhecer"])

    def test_meetings_and_named_team_members_go_to_implementation(self):
        for message in ["Confirmar reunião", "A reunião seria hoje as 12:00 não estou conseguindo entrar",
                        "Confirma por favor o horário da minha reunião hoje?",
                        "Gostaria de tirar uma dúvida rápida com o Carlos por gentileza",
                        "Sobre a reunião de amanhã às 11 horas que teremos com ele"]:
            self.assertRoute("implementation", message)

    def test_account_billing_divergence_goes_to_billing(self):
        self.assertRoute("billing", "Valores que estão divergindo")
        self.assertRoute("billing", "Mais veio 416,90", ["Meu boleto 397", "Valor"])
        self.assertRoute("billing", "Tá errado", ["Segunda via do boleto", "Que pagar o boleto", "Não to conseguindo"])

    def test_closing_messages_end_the_turn(self):
        self.assertRoute("closing", "Salomão, vou encaminhar a foto e, por algum motivo, agora entrou")
        self.assertRoute("closing", "O aplicativo só precisa do incentivo do técnico kkkk\nObrigado pela atenção")
        self.assertRoute(None, "Obrigado pela atenção", spoke=False)
        self.assertRoute(None, "obrigado, mas ainda não consegui entrar")

    def test_product_questions_are_not_captured_by_routes(self):
        for message in ["Como cancelar uma inscrição de evento na plataforma?",
                        "Como o membro cancela a assinatura de um plano de assinatura?",
                        "Como preencher o relatório da reunião da célula?", "Quero cancelar um boleto",
                        "Como cobrar valor diferente por lote de ingresso?", "Como cadastro um membro?",
                        "Não consigo ver as células em que sou líder", "2", "Nao"]:
            self.assertRoute(None, message)
        self.assertRoute(None, "Qual o valor mínimo para doação no app?", ["como configuro doações"])


class BotTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = DeliveryStore(Path(folder.name) / "delivery.sqlite3")
        self.agent = MagicMock()
        self.agent.process_message.return_value = {"response": "Orientação", "answer_status": "answered",
                                                   "scope_policy_version": module.SCOPE_POLICY_VERSION}
        self.bot = module.HubSpotSalomaoBot(self.store, self.agent, debounce_seconds=0)
        self.now = datetime.now(timezone.utc)

    def at(self, seconds):
        return (self.now + timedelta(seconds=seconds)).isoformat()

    def remember(self, *rows):
        self.store.remember_messages("t", [{"id": key, "created_at": self.at(offset), "text": text,
                                            "is_from_visitor": visitor} for key, offset, text, visitor in rows])

    def message(self, text, key="current", offset=0, attachments=None):
        return {"id": key, "text": text, "created_at": self.at(offset), "raw": {"attachments": attachments or []}}

    def test_routed_request_is_handed_off_with_a_specific_message_and_no_model(self):
        result = self.bot.process_message("t", self.message("Preciso saber como faço para cancelar ou suspender o contrato"))
        self.assertTrue(result["transfer_requested"])
        self.assertIn("contrato", result["response"])
        self.assertNotIn("inscrição", result["response"])
        self.agent.process_message.assert_not_called()

    def test_price_follow_up_after_overview_is_commercial(self):
        self.remember(("a", -60, "Eu não conheço nada sobre a plataforma, gostaria de conhecer", True),
                      ("b", -50, "*Visão geral da inChurch* ...", False))
        result = self.bot.process_message("t", self.message("Qual é o valor?"))
        self.assertEqual(result["answer_status"], "routed_commercial")
        self.agent.process_message.assert_not_called()

    def test_closing_reply_does_not_search_articles(self):
        self.remember(("a", -60, "Porém, quando tento entrar nele, aparece uma mensagem de erro", True),
                      ("b", -50, "Envie uma captura da mensagem exibida ao tocar em Entrar.", False))
        result = self.bot.process_message("t", self.message("Salomão, vou encaminhar a foto e, por algum motivo, agora entrou"))
        self.assertFalse(result["transfer_requested"])
        self.assertIn("Que bom", result["response"])
        self.agent.process_message.assert_not_called()

    def test_second_consecutive_clarification_becomes_a_handoff(self):
        self.remember(("a", -60, "Gostaria de saber como funciona", True), ("b", -50, SCOPE_CLARIFY, False))
        self.agent.process_message.return_value = {"response": SCOPE_CLARIFY, "answer_status": "clarification",
                                                   "scope_policy_version": module.SCOPE_POLICY_VERSION}
        result = self.bot.process_message("t", self.message("Tudo"))
        self.assertTrue(result["transfer_requested"])
        self.assertIn("Vou encaminhar", result["response"])

    def test_identical_image_refusal_is_not_repeated(self):
        refusal = "Só consigo analisar prints de telas da inChurch."
        self.remember(("a", -60, "O que aparece na tela e no relatório", True), ("b", -50, refusal, False))
        self.agent.process_message.return_value = {"response": refusal, "answer_status": "out_of_scope",
                                                   "scope_policy_version": module.SCOPE_POLICY_VERSION}
        result = self.bot.process_message("t", self.message(""))
        self.assertTrue(result["transfer_requested"])

    def test_text_and_two_prints_are_one_turn_and_one_image_is_analysed(self):
        image = {"url": "https://api.hubapi.com/file", "mimeType": "image/png"}
        pending = [self.message("O que aparece na tela e no relatório", "a", 0),
                   self.message("", "b", 1, [image]), self.message("", "c", 2, [image])]
        groups = self.bot._coalesce_pending("t", pending)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["source_message_ids"], ["a", "b", "c"])
        with patch.object(self.bot, "_download_attachment_as_base64", return_value="YWJj"):
            self.bot.process_message("t", groups[0])
        self.assertEqual(self.agent.process_message.call_count, 1)
        self.assertEqual(self.agent.process_message.call_args.kwargs["image_base64"], "YWJj")

    def test_model_unavailable_hands_over_instead_of_dead_end(self):
        self.agent.process_message.return_value = {"response": "Não consegui validar", "answer_status": "scope_blocked",
                                                   "scope_policy_version": module.SCOPE_POLICY_VERSION}
        result = self.bot.process_message("t", self.message("Como cadastro um membro?"))
        self.assertTrue(result["transfer_requested"])


class OpeningRequestTests(unittest.TestCase):
    """49545550850, 49546361109, 49574262093, 49210551153, 49051141666."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = DeliveryStore(Path(folder.name) / "delivery.sqlite3")
        self.agent = MagicMock()
        self.agent.process_message.return_value = {
            "response": "Para acessar seus relatórios, entre no aplicativo da sua igreja.",
            "answer_status": "answered", "scope_policy_version": module.SCOPE_POLICY_VERSION}
        self.bot = module.HubSpotSalomaoBot(self.store, self.agent, debounce_seconds=0)
        self.now = datetime.now(timezone.utc) - timedelta(seconds=10)
        self.ticket = {"properties": {
            "hs_pipeline": module.SALOMAO_PIPELINE, "hs_pipeline_stage": module.SALOMAO_STATUS,
            "hubspot_owner_id": module.SALOMAO_ACTOR_ID.removeprefix("A-"),
            module.SALOMAO_ENTRY_PROPERTY: (self.now - timedelta(seconds=30)).isoformat()}}
        self.messages = []
        patch.object(module, "get_thread_messages", side_effect=lambda *a, **kw: self.messages).start()
        patch.object(module, "get_ticket_by_id", side_effect=lambda *a: self.ticket).start()
        self.send = patch.object(module, "reply_to_visitor", side_effect=lambda thread, text: {
            "id": f"sent-{self.send.call_count}", "createdAt": self.now.isoformat()}).start()
        self.transfer = patch.object(module, "transfer_ticket_to_human_support", return_value=True).start()
        self.note = patch.object(module, "create_ticket_handoff_note", return_value={"id": "note"}).start()
        self.addCleanup(patch.stopall)

    def add(self, key, offset, text, incoming=True):
        self.messages.append({"id": key, "createdAt": (self.now + timedelta(seconds=offset)).isoformat(),
            "type": "MESSAGE", "text": text, "direction": "INCOMING" if incoming else "OUTGOING",
            "senders": [{"actorId": "V-client" if incoming else module.SALOMAO_ACTOR_ID}]})

    def test_need_described_before_the_menu_is_answered_without_generic_greeting(self):
        self.add("need", -240, "Me enviaram um e-mail informando q estou com 6 relatórios de celula atrasados. "
                               "Eu sou muito correta com as coisas mas n̈ estou conseguindo ajuda.")
        self.add("menu", -230, MENU, False)
        self.add("choice", -180, "1 ou 2 n̈sei")
        self.bot.process_thread("thread", "ticket")
        self.bot.process_thread("thread", "ticket")
        self.agent.process_message.assert_called_once()
        self.assertIn("relatórios", self.agent.process_message.call_args.kwargs["message"])
        sent = [call.args[1] for call in self.send.call_args_list]
        self.assertEqual(len(sent), 1)
        self.assertNotEqual(sent[0], self.bot.ENTRY_GREETING)

    def test_opening_sales_request_is_handed_off_at_entry(self):
        self.add("need", -240, "Gostaria de conhecer os planos e os valor da plataforma para implantação na minha igreja.")
        self.add("menu", -230, MENU, False)
        self.add("choice", -180, "2")
        self.bot.process_thread("thread", "ticket")
        self.agent.process_message.assert_not_called()
        self.assertIn("planos e valores", self.send.call_args.args[1])
        self.transfer.assert_called_once()

    def test_greeting_and_menu_choice_alone_keep_the_single_greeting(self):
        self.add("hello", -240, "Bom dia, a paz do Senhor Jesus")
        self.add("menu", -230, MENU, False)
        self.add("choice", -180, "2")
        self.bot.process_thread("thread", "ticket")
        self.send.assert_called_once_with("thread", self.bot.ENTRY_GREETING)
        self.agent.process_message.assert_not_called()

    def test_answered_opening_waits_for_the_next_customer_message(self):
        self.add("need", -240, "Eu estou com problemas de entrar no meu aplicativo da Igreja, informa que deu erro")
        self.add("menu", -230, MENU, False)
        self.bot.process_thread("thread", "ticket")
        self.bot.process_thread("thread", "ticket")
        self.assertEqual(self.send.call_count, 1)
        self.add("next", 5, "Aparece usuário não cadastrado")
        self.bot.process_thread("thread", "ticket")
        self.assertEqual(self.send.call_count, 2)


class AgentTests(unittest.TestCase):
    def test_whatsapp_never_receives_article_excerpts(self):
        supervisor = agent_module.SalomaoSupervisorAgent(session_id="t", user_metadata={"originating_channel": "whatsapp"})
        docs = [{"id": "e", "title": "Eventos | Como realizar o cancelamento de uma inscrição",
                 "url": "https://portal.inchurch.com.br/pt-br/eventos-como-cancelar-um-ticket", "content": "Cancelar inscrição."}]
        with patch.object(agent_module.knowledge_base, "search", return_value=docs), patch.object(agent_module, "Agent") as model:
            model.return_value.run.return_value = SimpleNamespace(status="COMPLETED", content={
                "answer": "Resposta sem fonte", "source_ids": []})
            result = supervisor.run_pipeline(message="como faço para suspender")
        self.assertEqual(result.answer_status, "no_match")
        self.assertTrue(result.requires_human_handoff)
        self.assertNotIn("Orientações da documentação", result.message)

    def test_malformed_answer_does_not_back_off_every_customer(self):
        agent_module._answer_model_retry_at = 0.0
        supervisor = agent_module.SalomaoSupervisorAgent(session_id="t", user_metadata={})
        docs = [{"id": "r", "title": "Estorno", "url": "https://portal.inchurch.com.br/pt-br/estorno", "content": "Acesse Financeiro."}]
        with patch.object(agent_module.knowledge_base, "search", return_value=docs), patch.object(agent_module, "Agent") as model:
            model.return_value.run.return_value = SimpleNamespace(status="COMPLETED", content="not-json")
            supervisor.run_pipeline(message="como fazer estorno")
        self.assertEqual(agent_module._answer_model_retry_at, 0.0)

    def test_short_reply_to_salomao_question_is_a_continuation(self):
        history = [{"role": "user", "content": "Mais veio 416,90"},
                   {"role": "assistant", "content": "No boleto aparece algum detalhamento de multa ou juros?"}]
        self.assertTrue(agent_module._answers_previous_question(history))
        self.assertFalse(agent_module._answers_previous_question(history[:1]))

    def test_classifier_failure_is_not_treated_as_continuation(self):
        self.assertFalse(agent_module.TextScopeResult(classified=False).classified)


class ContextAndNoteTests(unittest.TestCase):
    def test_turns_from_days_ago_are_not_current_context(self):
        now = datetime.now(timezone.utc)
        old = {"id": "old", "text": "A reunião seria hoje as 12:00", "is_from_visitor": True,
               "created_at": (now - timedelta(days=4)).isoformat()}
        recent = {"id": "recent", "text": "Bom dia", "is_from_visitor": True,
                  "created_at": (now - timedelta(minutes=1)).isoformat()}
        current = {"id": "current", "created_at": now.isoformat()}
        self.assertEqual([m["content"] for m in history_before([old, recent], current)], ["Bom dia"])

    def test_handoff_note_reports_only_salomao_guidance(self):
        now = datetime.now(timezone.utc).isoformat()
        messages = [{"id": "menu", "text": MENU, "is_from_visitor": False, "created_at": now},
                    {"id": "agent", "text": "Link da videochamada: https://meet.google.com/abc", "is_from_visitor": False, "created_at": now},
                    {"id": "own", "text": "Qual é o nome exato do aplicativo?", "is_from_visitor": False, "created_at": now},
                    {"id": "c", "text": "Reunião", "is_from_visitor": True, "created_at": now}]
        note = build_handoff_note(thread_id="t", message_id="m", messages=messages, reason="r", assistant_ids={"own"})
        self.assertIn("nome exato do aplicativo", note)
        self.assertNotIn("meet.google.com", note)
        self.assertNotIn("Digite apenas", note)


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = DeliveryStore(Path(folder.name) / "delivery.sqlite3")
        self.agent = MagicMock()
        self.bot = module.HubSpotSalomaoBot(self.store, self.agent, debounce_seconds=0)
        self.now = datetime.now(timezone.utc)

    def test_second_thanks_is_a_goodbye_not_a_handoff(self):
        self.store.remember_messages("t", [
            {"id": "q", "created_at": (self.now - timedelta(seconds=60)).isoformat(), "text": "agora entrou", "is_from_visitor": True},
            {"id": "c", "created_at": (self.now - timedelta(seconds=50)).isoformat(),
             "text": "Que bom! Fico à disposição. Se precisar de algo mais, é só me chamar por aqui.", "is_from_visitor": False}])
        result = self.bot.process_message("t", {"id": "m", "text": "Obrigado", "created_at": self.now.isoformat(), "raw": {}})
        self.assertFalse(result["transfer_requested"])
        self.assertEqual(result["response"], self.bot.CLOSING_AGAIN)

    def test_two_voice_notes_are_never_merged(self):
        audio = {"url": "https://api.hubapi.com/a.ogg", "mimeType": "audio/ogg"}
        pending = [{"id": "a", "text": "", "created_at": self.now.isoformat(), "raw": {"attachments": [audio]}},
                   {"id": "b", "text": "", "created_at": (self.now + timedelta(seconds=1)).isoformat(), "raw": {"attachments": [audio]}}]
        self.assertEqual(len(self.bot._coalesce_pending("t", pending)), 2)

    def test_spoken_contract_request_is_routed(self):
        self.agent.process_message.return_value = {"response": "Resposta", "answer_status": "answered",
            "audio_transcription": "quero cancelar o contrato da igreja", "scope_policy_version": module.SCOPE_POLICY_VERSION}
        audio = {"url": "https://api.hubapi.com/a.ogg", "mimeType": "audio/ogg"}
        with patch.object(self.bot, "_download_attachment_as_base64", return_value="YWJj"):
            result = self.bot.process_message("t", {"id": "m", "text": "", "created_at": self.now.isoformat(),
                                                    "raw": {"attachments": [audio]}})
        self.assertEqual(result["answer_status"], "routed_contract")
        self.assertTrue(result["transfer_requested"])

    def test_router_does_not_capture_lookalike_product_questions(self):
        for message, recent in [("Como encerrar a transmissão na plataforma?", []),
                                ("Como cancelar a transmissão ao vivo na plataforma?", []),
                                ("Tenho uma dúvida com o Pix", []),
                                ("A senha não bate com a que eu cadastrei", ["Segunda via do boleto"]),
                                ("O nome do evento ficou errado no app", ["Qual a taxa do evento?"])]:
            self.assertIsNone(route_intent(message, recent, assistant_spoke=True), message)
        self.assertEqual(route_intent("Como está a implantação na minha igreja?").intent, "implementation")

    def test_empty_answer_does_not_back_off_every_customer(self):
        agent_module._answer_model_retry_at = 0.0
        supervisor = agent_module.SalomaoSupervisorAgent(session_id="t", user_metadata={})
        docs = [{"id": "r", "title": "Estorno", "url": "https://portal.inchurch.com.br/pt-br/estorno", "content": "Acesse Financeiro."}]
        with patch.object(agent_module.knowledge_base, "search", return_value=docs), patch.object(agent_module, "Agent") as model:
            model.return_value.run.return_value = SimpleNamespace(status="COMPLETED", content="")
            supervisor.run_pipeline(message="como fazer estorno")
        self.assertEqual(agent_module._answer_model_retry_at, 0.0)

    def test_entry_greeting_is_not_a_question_being_answered(self):
        history = [{"role": "assistant", "content": module.HubSpotSalomaoBot.ENTRY_GREETING}]
        self.assertFalse(agent_module._answers_previous_question(history))


class HumanQueueDestinationTests(unittest.TestCase):
    """Every handoff, whatever its reason, goes to Suporte N1 / Novo and clears the owner."""

    N1 = {"hs_pipeline": "636459134", "hs_pipeline_stage": "939275049", "hubspot_owner_id": ""}

    def setUp(self):
        import hubspot_service
        self.hs = hubspot_service
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = DeliveryStore(Path(folder.name) / "delivery.sqlite3")
        self.agent = MagicMock()
        self.agent.process_message.return_value = {"response": "x", "answer_status": "no_match",
                                                   "scope_policy_version": module.SCOPE_POLICY_VERSION}
        self.bot = module.HubSpotSalomaoBot(self.store, self.agent, debounce_seconds=0)
        self.now = datetime.now(timezone.utc) - timedelta(seconds=10)
        self.ticket = {"properties": {"hs_pipeline": module.SALOMAO_PIPELINE, "hs_pipeline_stage": module.SALOMAO_STATUS,
                                      "hubspot_owner_id": module.SALOMAO_ACTOR_ID.removeprefix("A-")}}
        self.messages = []
        patch.object(module, "get_thread_messages", side_effect=lambda *a, **kw: self.messages).start()
        patch.object(module, "get_ticket_by_id", side_effect=lambda *a: self.ticket).start()
        patch.object(module, "reply_to_visitor", side_effect=lambda thread, text: {
            "id": "sent-" + str(len(self.messages)), "createdAt": self.now.isoformat()}).start()
        patch.object(module, "create_ticket_handoff_note", return_value={"id": "note"}).start()
        # The real transfer function runs; only the HTTP call is intercepted.
        self.patch = patch.object(self.hs.requests, "patch", return_value=SimpleNamespace(status_code=200)).start()
        self.addCleanup(patch.stopall)

    def test_configured_human_queue_is_suporte_n1_novo(self):
        self.assertEqual((self.hs.HUMAN_SUPPORT_PIPELINE, self.hs.HUMAN_SUPPORT_STAGE), ("636459134", "939275049"))
        self.assertTrue(self.hs.transfer_to_human("t1"))
        self.assertEqual(self.patch.call_args.kwargs["json"], {"properties": self.N1})

    def test_bot_has_no_other_ticket_move(self):
        for name in ("update_ticket_pipeline_status", "update_ticket_owner", "transfer_to_human"):
            self.assertFalse(hasattr(module, name), name)
        import intent_routing
        self.assertFalse(any(hasattr(intent_routing, n) for n in ("requests", "hubspot_service", "transfer_ticket_to_human_support")))

    def assertHandoffToN1(self, text, agent_result=None):
        if agent_result:
            self.agent.process_message.return_value = agent_result
        self.messages.append({"id": f"m{len(self.messages)}", "createdAt": self.now.isoformat(), "type": "MESSAGE",
                              "text": text, "direction": "INCOMING", "senders": [{"actorId": "V-client"}]})
        self.patch.reset_mock()
        self.bot.process_thread("thread-" + str(abs(hash(text))), "ticket")
        self.assertEqual(self.patch.call_count, 1, text)
        self.assertTrue(self.patch.call_args.args[0].endswith("/crm/v3/objects/tickets/ticket"))
        self.assertEqual(self.patch.call_args.kwargs["json"], {"properties": self.N1}, text)

    def test_every_handoff_reason_uses_the_same_queue(self):
        cases = [
            ("Quero falar com um atendente", None),                               # explicit request
            ("Preciso cancelar o contrato", None),                                # contract
            ("Quero assinar a plataforma para minha igreja", None),               # commercial
            ("Confirmar reunião", None),                                          # implementation
            ("Valores que estão divergindo", None),                               # billing
            ("Como configuro o módulo X?", None),                                 # no_match
            ("Como configuro o módulo Y?", {"response": "x", "answer_status": "unavailable",
                                            "scope_policy_version": module.SCOPE_POLICY_VERSION}),  # model down
            ("Como configuro o módulo Z?", {"response": "x", "answer_status": "scope_blocked",
                                            "scope_policy_version": module.SCOPE_POLICY_VERSION}),  # guard blocked
        ]
        for text, result in cases:
            with self.subTest(text=text):
                self.messages = []
                self.assertHandoffToN1(text, result)


if __name__ == "__main__":
    unittest.main()
