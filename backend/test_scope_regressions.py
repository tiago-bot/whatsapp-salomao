"""Offline regression tests: no external calls or production writes."""
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Allow a clean checkout to run the suite without real credentials.
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("PINECONE_API_KEY", "test-key")
os.environ.setdefault("PINECONE_HOST", "https://test.svc.example.com")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")

import salomao_agent as module
from database import ConversationDatabase


class ScopeRegressions(unittest.TestCase):
    def setUp(self):
        self.agent = module.SalomaoAgent()

    def test_questions_from_production_logs_reach_knowledge_pipeline(self):
        questions = [
            "pedidos de oração",
            "como funciona a área de pedidos de oração?",
            "Como encerro o contrato com a In Church?",
            "cancelamento",
            "quero fazer estorno",
            "Esqueci minha senha",
            "Olá!",
        ]
        for question in questions:
            with self.subTest(question=question), patch.object(module, "Agent") as classifier:
                scope = self.agent._classify_text_scope(question)
                self.assertEqual(scope.status, module.ImageScopeStatus.INCHURCH)
                classifier.assert_not_called()

    def test_unknown_words_and_followups_get_semantic_review_with_recent_context(self):
        for question in ["não achei essa opção", "como publico uma transmissão?", "receita por categoria", "Quem ganhou a copa?"]:
            with self.subTest(question=question), patch.object(module, "Agent") as classifier:
                classifier.return_value.run.return_value = SimpleNamespace(content='{"status":"uncertain","confidence":0.4}')
                scope = self.agent._classify_text_scope(question, "Cliente: Como criar um evento?\nSalomao: Abra Eventos.")
                payload = json.loads(classifier.return_value.run.call_args.args[0])
                self.assertIn("Abra Eventos", payload["recent_history"])
                self.assertEqual(payload["message"], question)
                self.assertEqual(scope.status, module.ImageScopeStatus.UNCERTAIN)

    def test_classifier_failure_or_malformed_output_does_not_reject_customer(self):
        for content in ["not json", '{"status":"invalid"}', '{"status":"out_of_scope","confidence":2}']:
            with self.subTest(content=content), patch.object(module, "Agent") as classifier:
                classifier.return_value.run.return_value = SimpleNamespace(content=content)
                self.assertEqual(self.agent._classify_text_scope("não consegui").status, module.ImageScopeStatus.UNCERTAIN)
        with patch.object(module, "Agent", side_effect=TimeoutError("test")):
            self.assertEqual(self.agent._classify_text_scope("não consegui").status, module.ImageScopeStatus.UNCERTAIN)

    def test_process_message_only_blocks_confident_external_intent(self):
        for status, confidence, blocked in [
            (module.ImageScopeStatus.INCHURCH, 1, False),
            (module.ImageScopeStatus.UNCERTAIN, 0.8, False),
            (module.ImageScopeStatus.OUT_OF_SCOPE, 0.5, False),
            (module.ImageScopeStatus.OUT_OF_SCOPE, 0.98, True),
        ]:
            with self.subTest(status=status, confidence=confidence), patch.object(module, "db") as db, patch.object(module, "SalomaoSupervisorAgent") as supervisor, patch.object(self.agent, "_classify_text_scope", return_value=module.TextScopeResult(status=status, confidence=confidence)), patch.object(self.agent, "refresh_conversation_summary"), patch.object(self.agent, "_record_turn_metric"):
                db.get_message_count.return_value = 0
                db.get_conversation_history.return_value = []
                db.add_message.return_value = {"id": "message-local"}
                supervisor.return_value.run_pipeline.return_value = module.SalomaoPipelineResponse(message="Resposta da base", model_name="test")
                result = self.agent.process_message("como faço?", session_id="local-test")
                self.assertEqual(result["model_used"] == "scope_guard", blocked)
                self.assertEqual(supervisor.called, not blocked)
                # Rejected turns must also be available for a subsequent clarification.
                self.assertEqual(db.add_message.call_count, 2)

    def test_uncertain_screenshot_continues_to_pipeline(self):
        with patch.object(module, "db") as db, patch.object(module, "SalomaoSupervisorAgent") as supervisor, patch.object(self.agent, "_classify_image_scope", return_value=module.ImageScopeResult()), patch.object(self.agent, "refresh_conversation_summary"), patch.object(self.agent, "_record_turn_metric"):
            db.get_message_count.return_value = 0
            db.get_conversation_history.return_value = []
            db.add_message.return_value = {}
            supervisor.return_value.run_pipeline.return_value = module.SalomaoPipelineResponse(message="Qual tela você está usando?", model_name="test")
            result = self.agent.process_message("receita", image_base64="aW1hZ2U=", session_id="local-image-test")
            self.assertEqual(result["model_used"], "test")
            supervisor.return_value.run_pipeline.assert_called_once()

    def test_recent_messages_are_returned_in_chronological_order(self):
        database = object.__new__(ConversationDatabase)
        database.client = MagicMock()
        query = database.client.table.return_value.select.return_value.eq.return_value
        query.order.return_value.limit.return_value.execute.return_value.data = [
            {"content": "latest"}, {"content": "previous"},
        ]
        self.assertEqual(database.get_conversation_history("test", limit=2), [{"content": "previous"}, {"content": "latest"}])
        query.order.assert_called_once_with("created_at", desc=True)
        query.order.return_value.limit.assert_called_once_with(2)

    def test_provider_errors_are_not_returned_as_successful_answers(self):
        supervisor = object.__new__(module.SalomaoSupervisorAgent)
        supervisor.rag_agent = MagicMock()
        supervisor.team = MagicMock()
        supervisor.rag_agent.run.return_value = SimpleNamespace(status="ERROR", content="Incorrect API key provided: SECRET")
        result = supervisor._run_fast_knowledge_path(
            message="quero fazer estorno", triage=module.heuristic_triage("estorno"), start=0,
        )
        self.assertEqual(result.error, "model_unavailable")
        self.assertNotIn("SECRET", result.message)
        supervisor.team.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
