"""Claim-level groundedness gate, event-ID detection and per-conversation locks."""
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import groundedness
import hubspot_bot
import salomao_agent as m

ARTICLE = {"id": "control", "title": "O que é o App inChurch Control e como usá-lo?",
           "url": "https://portal.inchurch.com.br/pt-br/control", "score": 0.775,
           "content": "O inChurch Control é o aplicativo de gestão para administradores."}


class GroundednessCheckTests(unittest.TestCase):
    def check(self, content=None, status="COMPLETED", side_effect=None, articles=(ARTICLE,)):
        with patch.object(groundedness, "GROUNDEDNESS_CHECK_ENABLED", True), patch.object(groundedness, "Agent") as agent:
            if side_effect:
                agent.side_effect = side_effect
            agent.return_value.run.return_value = SimpleNamespace(status=status, content=content)
            return groundedness.answer_is_grounded("Inchurch control é isso?", "Sim.", list(articles))

    def test_only_an_explicit_full_approval_passes(self):
        self.assertTrue(self.check({"supported": True, "unsupported_claims": []}))
        self.assertFalse(self.check({"supported": True, "unsupported_claims": ["o app é o da igreja"]}))
        self.assertFalse(self.check({"supported": False}))

    def test_any_failure_holds_the_answer(self):
        for kwargs in [{"content": "not-json"}, {"content": {"supported": "true"}}, {"content": {}},
                       {"content": {"supported": True}, "status": "ERROR"}, {"side_effect": TimeoutError("secret")}]:
            with self.subTest(kwargs=kwargs):
                self.assertFalse(self.check(**kwargs))

    def test_answer_without_cited_articles_is_never_grounded(self):
        self.assertFalse(self.check({"supported": True}, articles=()))

    def test_disabled_check_does_not_call_a_model(self):
        with patch.object(groundedness, "GROUNDEDNESS_CHECK_ENABLED", False), patch.object(groundedness, "Agent") as agent:
            self.assertTrue(groundedness.answer_is_grounded("q", "a", [ARTICLE]))
            agent.assert_not_called()


class PipelineGateTests(unittest.TestCase):
    def setUp(self):
        m._answer_model_retry_at = 0.0
        self.supervisor = m.SalomaoSupervisorAgent(session_id="t", user_metadata={"originating_channel": "whatsapp"})
        other = {**ARTICLE, "id": "store", "title": "Publicação na App Store", "score": 0.695}
        self.search = patch.object(m.knowledge_base, "search", return_value=[ARTICLE, other]).start()
        model = patch.object(m, "Agent").start()
        model.return_value.run.return_value = SimpleNamespace(status="COMPLETED", content={
            "answer": "O inChurch Control é o aplicativo de gestão para administradores.", "source_ids": ["control"]})
        self.addCleanup(patch.stopall)

    def test_unsupported_answer_is_handed_over(self):
        with patch.object(m.groundedness, "answer_is_grounded", return_value=False) as check:
            result = self.supervisor.run_pipeline(message="Inchurch control é isso?")
        self.assertEqual(result.answer_status, "no_match")
        self.assertTrue(result.requires_human_handoff)
        # Only the cited article is evidence; retrieved-but-uncited ones are not.
        self.assertEqual([a["id"] for a in check.call_args.args[2]], ["control"])

    def test_supported_answer_is_delivered_with_its_source(self):
        with patch.object(m.groundedness, "answer_is_grounded", return_value=True):
            result = self.supervisor.run_pipeline(message="Inchurch control é isso?")
        self.assertEqual(result.answer_status, "answered")
        self.assertIn("Fonte:", result.message)

    def test_grounded_answer_effort_is_configurable_alone(self):
        with patch.dict("os.environ", {"GROUNDED_REASONING_EFFORT": "medium"}), \
                patch.object(m, "OpenAIChat") as chat, patch.object(m.groundedness, "answer_is_grounded", return_value=True):
            self.supervisor.run_pipeline(message="Inchurch control é isso?")
        if m.DEFAULT_MODEL.startswith("gpt-5"):
            self.assertEqual(chat.call_args.kwargs["reasoning_effort"], "medium")


class EventIdTests(unittest.TestCase):
    def setUp(self):
        self.agent = m.SalomaoAgent()
        # The InRadar lookup is the external call a false event ID would trigger.
        self.tool = patch.object(m, "fetch_event_details", return_value={"name": "Evento"}).start()
        patch.object(m, "analyze_event_visibility", return_value={"status": "ok"}).start()
        self.addCleanup(patch.stopall)

    def test_bare_numbers_are_not_event_ids(self):
        for text in ["Meu WhatsApp 11986064917", "Meu boleto 397", "Segue o protocolo 49404473653",
                     "O pedido 1234567 não chegou", "CNPJ 33175142000149", "Ligue 3031609"]:
            with self.subTest(text=text):
                self.assertIsNone(self.agent._check_for_event_diagnosis(text))
        self.tool.assert_not_called()

    def test_explicit_event_references_are_diagnosed(self):
        for text, expected in [("O evento 1234567 não aparece", 1234567), ("ID do evento: 765432", 765432),
                               ("https://admin.inchurch.com.br/eventos/evento/98765/", 98765)]:
            with self.subTest(text=text):
                self.tool.reset_mock()
                self.assertIsNotNone(self.agent._check_for_event_diagnosis(text))
                self.tool.assert_called_once_with(expected)


class ConversationLockTests(unittest.TestCase):
    def test_different_conversations_never_share_a_lock(self):
        bot = hubspot_bot.HubSpotSalomaoBot(store=MagicMock(), agent=MagicMock(), memory=None, debounce_seconds=0)
        first = bot._thread_mutex("thread-a")
        held = [bot._thread_mutex(f"thread-{i}") for i in range(500)]
        self.assertTrue(first.acquire(blocking=False))
        self.assertTrue(all(lock is first or lock.acquire(blocking=False) for lock in held))
        self.assertIs(bot._thread_mutex("thread-a"), first)


if __name__ == "__main__":
    unittest.main()
