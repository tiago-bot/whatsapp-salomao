"""Behavioral regressions with no external calls or customer records."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

for key, value in {"OPENAI_API_KEY": "test-key", "PINECONE_API_KEY": "test-key",
                   "PINECONE_HOST": "https://test.svc.example.com", "SUPABASE_URL": "https://test.supabase.co",
                   "SUPABASE_KEY": "test-key"}.items():
    os.environ.setdefault(key, value)

import salomao_agent as m
from published_knowledge import PublishedKnowledge, _OfficialArticleParser, contextual_query, safe_url
from knowledge_base import KnowledgeBase

DOCS = [
    {"id": "refund", "title": "Financeiro | Estornos de transações", "content": "O estorno devolve o valor. Acesse Financeiro > Entradas para localizar a transação.", "url": "https://portal.inchurch.com.br/pt-br/estorno", "category": "Financeiro", "updated_at": "2026-08-01"},
    {"id": "prayer", "title": "Cuidado | Pedidos de oração", "content": "Pedidos de oração são enviados no aplicativo e ficam disponíveis para a equipe.", "url": "https://portal.inchurch.com.br/pt-br/oracao", "category": "Intercessão", "updated_at": "2026-08-01"},
    {"id": "external", "title": "App externo", "content": "Como habilitar um app externo.", "url": "https://portal.inchurch.com.br/pt-br/app", "category": "App", "updated_at": "2026-08-01"},
]


class GroundedAnswers(unittest.TestCase):
    def setUp(self):
        self.supervisor = m.SalomaoSupervisorAgent(session_id="test", user_metadata={})
        self.catalog = PublishedKnowledge()
        self.load = patch.object(self.catalog, "_load", return_value=DOCS).start()
        self.addCleanup(patch.stopall)
        patch.object(m, "_answer_model_retry_at", 0).start()

    def test_publication_search_does_not_need_embeddings(self):
        for q, expected in [("quero fazer estorno", "refund"), ("Como pedir reembolso?", "refund"), ("pedidos de oração", "prayer")]:
            self.assertEqual(self.catalog.search(q)[0]["id"], expected)

    def test_short_followup_keeps_topic_but_new_topic_replaces_it(self):
        history = "Cliente: quero fazer estorno\nSalomao: Financeiro > Entradas."
        self.assertEqual(self.catalog.search("não aparece o botão", history)[0]["id"], "refund")
        self.assertEqual(contextual_query("pedidos de oração", history), "pedidos de oração")
        self.assertEqual(contextual_query("de evento", history), "quero fazer estorno de evento")
        self.assertEqual(contextual_query("Inscrição em evento", "Cliente: cancelamento\nSalomao: O que deseja cancelar? Inscrição em evento ou boleto?"), "cancelamento Inscrição em evento")

    def test_pinecone_metadata_matches_production_schema_and_gets_full_article(self):
        kb = object.__new__(KnowledgeBase)
        kb.index = MagicMock()
        kb._get_embedding = MagicMock(return_value=[0.1])
        kb.index.query.return_value.matches = [SimpleNamespace(id="chunk-0", score=.8, metadata={
            "article_id": "refund", "article_title": "Título original", "article_url": DOCS[0]["url"],
            "category_name": "Financeiro", "text": "Incomplete chunk"})]
        with patch("knowledge_base.published_knowledge") as published:
            published.search.return_value = []
            published.by_url.return_value = {DOCS[0]["url"]: DOCS[0]}
            published.hydrate_official_article.return_value = ""
            result = kb.search("extorno")
            self.assertEqual(result[0]["content"], DOCS[0]["content"])
            self.assertEqual(result[0]["url"], DOCS[0]["url"])
            published.by_url.return_value = {}
            result = kb.search("extorno")
            self.assertEqual(result[0]["title"], "Título original")
            self.assertEqual(result[0]["category"], "Financeiro")

    def test_legacy_chunks_are_reassembled_in_article_order_then_hydrated(self):
        kb = object.__new__(KnowledgeBase)
        kb.index = MagicMock()
        kb._get_embedding = MagicMock(return_value=[0.1])
        first = SimpleNamespace(id="refund-2", score=.9, metadata={
            "article_id": "refund", "article_title": "Estorno", "article_url": DOCS[0]["url"],
            "chunk_index": 2, "text": "Segundo passo."})
        chunks = [first, SimpleNamespace(id="refund-1", score=.8, metadata={
            "article_id": "refund", "article_title": "Estorno", "article_url": DOCS[0]["url"],
            "chunk_index": 1, "text": "Primeiro passo."})]
        kb.index.query.side_effect = [SimpleNamespace(matches=[first]), SimpleNamespace(matches=chunks)]
        with patch("knowledge_base.published_knowledge") as published:
            published.search.return_value = []
            published.by_url.return_value = {}
            published.hydrate_official_article.return_value = "Artigo oficial completo."
            result = kb.search("estorno", top_k=1)
        self.assertEqual(result[0]["content"], "Artigo oficial completo.")
        self.assertEqual(result[0]["retrieval"], "official_live")
        self.assertEqual(kb.index.query.call_args_list[1].kwargs["filter"],
                         {"article_url": {"$eq": DOCS[0]["url"]}})

    def test_official_article_parser_excludes_navigation_and_scripts(self):
        parser = _OfficialArticleParser()
        parser.feed("<nav>Menu externo</nav><article class='knowledgebase-post'><h1>Estorno</h1>"
                    "<p>Acesse <strong>Financeiro &gt; Entradas</strong>.</p>"
                    "<script>segredo()</script></article><footer>Rodapé</footer>")
        content = parser.text()
        self.assertIn("Financeiro > Entradas", content)
        self.assertNotIn("Menu externo", content)
        self.assertNotIn("segredo", content)
        self.assertNotIn("Rodapé", content)

    def test_ambiguous_spelling_does_not_choose_unrelated_article(self):
        self.assertEqual(self.catalog.search("como fazer extorno"), [])

    def test_new_question_matching_only_incidental_word_does_not_retrieve(self):
        self.assertEqual(self.catalog.search("resultado campeonato financeiro futebol"), [])
        self.assertEqual(self.catalog.search("encerrar contrato"), [])

    def test_source_urls_must_be_official_https(self):
        for url in ["javascript:alert(1)", "https://portal.inchurch.com.br.evil.com/x", "https://evil.com/x"]:
            self.assertFalse(safe_url(url))

    def test_model_failure_returns_real_document_and_backs_off(self):
        with patch.object(m.knowledge_base, "search", return_value=DOCS[:1]), patch.object(m, "Agent") as agent:
            agent.return_value.run.return_value = SimpleNamespace(status="ERROR", content="secret provider error")
            first = self.supervisor.run_pipeline(message="como fazer estorno")
            second = self.supervisor.run_pipeline(message="quero fazer estorno")
            self.assertEqual(first.answer_status, "documentation")
            self.assertIn("Financeiro > Entradas", first.message)
            self.assertIn(DOCS[0]["url"], first.message)
            self.assertNotIn("secret", first.message)
            self.assertIsNone(first.error)
            self.assertEqual(second.answer_status, "documentation")
            agent.return_value.run.assert_called_once()

    def test_total_outage_is_an_error_without_fake_suggestions(self):
        with patch.object(m.knowledge_base, "search", side_effect=TimeoutError), patch.object(m, "Agent") as agent:
            agent.return_value.run.side_effect = TimeoutError
            result = self.supervisor.run_pipeline(message="como fazer estorno")
            self.assertEqual(result.answer_status, "unavailable")
            self.assertIsNotNone(result.error)
            self.assertEqual(result.suggested_actions, [])

    def test_answer_uses_only_retrieved_sources_and_one_model_call(self):
        with patch.object(m.knowledge_base, "search", return_value=DOCS[:1]), patch.object(m, "Agent") as agent:
            agent.return_value.run.return_value = SimpleNamespace(status="COMPLETED", content={
                "answer": "Acesse Financeiro > Entradas.", "source_ids": ["refund", "invented"],
                "needs_clarification": False, "suggested_actions": ["Sugestão genérica"]})
            result = self.supervisor.run_pipeline(message="quero fazer estorno")
            self.assertEqual([s["id"] for s in result.sources], ["refund"])
            self.assertEqual(result.answer_status, "answered")
            self.assertEqual(result.suggested_actions, [])
            agent.return_value.run.assert_called_once()

    def test_unsupported_answer_is_replaced_with_document_excerpt(self):
        with patch.object(m.knowledge_base, "search", return_value=DOCS[:1]), patch.object(m, "Agent") as agent:
            agent.return_value.run.return_value = SimpleNamespace(status="COMPLETED", content={
                "answer": "Invented procedure", "source_ids": ["invented"]})
            result = self.supervisor.run_pipeline(message="estorno")
            self.assertEqual(result.answer_status, "documentation")
            self.assertNotIn("Invented", result.message)

    def test_exact_bad_refund_answer_is_rejected_when_article_has_steps(self):
        bad = ("O estorno não é feito pela aba de Extrato. A documentação disponível "
               "não detalha os caminhos ou telas exatos para executar o estorno.")
        with patch.object(m.knowledge_base, "search", return_value=DOCS[:1]), patch.object(m, "Agent") as agent:
            agent.return_value.run.return_value = SimpleNamespace(status="COMPLETED", content={
                "answer": bad, "source_ids": ["refund"]})
            result = self.supervisor.run_pipeline(message="quero fazer estorno")
        self.assertEqual(result.answer_status, "documentation")
        self.assertIn("Financeiro > Entradas", result.message)
        self.assertNotIn("não detalha", result.message)

    def test_missing_object_gets_focused_clarification(self):
        with patch.object(m.knowledge_base, "search", return_value=[]), patch.object(m, "Agent") as agent:
            agent.return_value.run.return_value = SimpleNamespace(status="COMPLETED", content={
                "answer": "O que você deseja cancelar?", "source_ids": [],
                "needs_clarification": True, "suggested_actions": ["Uma inscrição", "O contrato"]})
            result = self.supervisor.run_pipeline(message="cancelamento")
            self.assertEqual(result.answer_status, "clarification")
            self.assertEqual(result.suggested_actions, ["Uma inscrição", "O contrato"])

    def test_clear_request_without_documentation_is_not_treated_as_outage(self):
        with patch.object(m.knowledge_base, "search", return_value=[]), patch.object(m, "Agent") as agent:
            agent.return_value.run.return_value = SimpleNamespace(status="COMPLETED", content={
                "answer": "A documentação não detalha o encerramento do contrato.",
                "insufficient_knowledge": True})
            result = self.supervisor.run_pipeline(message="Como encerro o contrato com a In Church?")
            self.assertEqual(result.answer_status, "no_match")
            self.assertIsNone(result.error)
            self.assertEqual(result.route, "CUSTOMER_SUCCESS")


if __name__ == "__main__":
    unittest.main()
