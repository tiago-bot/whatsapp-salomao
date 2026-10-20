"""Planning before the knowledge-base search, multi-query merge and relevance gate.

Scores and messages come from the read-only Pinecone measurement of 2026-10-20.
"""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import query_planning
import salomao_agent as m
from query_planning import QueryPlan


def doc(article_id, title, score, retrieval=None):
    article = {"id": article_id, "title": title, "score": score, "content": f"Acesse {title} e siga os passos.",
               "url": f"https://portal.inchurch.com.br/pt-br/{article_id}"}
    if retrieval:
        article["retrieval"] = retrieval
    return article


GROUNDED = SimpleNamespace(status="COMPLETED", content={"answer": "Acesse o menu e siga os passos.",
                                                         "source_ids": ["members"]})


class PlannerParsingTests(unittest.TestCase):
    def run_planner(self, content, status="COMPLETED"):
        with patch.object(query_planning, "QUERY_PLANNER_ENABLED", True), patch.object(query_planning, "Agent") as agent:
            agent.return_value.run.return_value = SimpleNamespace(status=status, content=content)
            return query_planning.plan_query("Como cadastro um membro?", "")

    def test_disabled_planner_never_calls_a_model(self):
        with patch.object(query_planning, "QUERY_PLANNER_ENABLED", False), patch.object(query_planning, "Agent") as agent:
            self.assertIsNone(query_planning.plan_query("Como cadastro um membro?"))
            agent.assert_not_called()

    def test_valid_plan_is_returned(self):
        plan = self.run_planner({"intent": "search", "query": "como cadastrar um membro"})
        self.assertEqual((plan.intent, plan.query), ("search", "como cadastrar um membro"))

    def test_search_without_query_keeps_the_customer_words(self):
        self.assertEqual(self.run_planner({"intent": "search"}).query, "Como cadastro um membro?")

    def test_failures_fall_back_to_direct_search(self):
        for content, status in [("not-json", "COMPLETED"), ({"intent": "other"}, "COMPLETED"),
                                ({"intent": "search", "query": "x"}, "ERROR"), ({"intent": "clarify"}, "COMPLETED")]:
            with self.subTest(content=content, status=status):
                self.assertIsNone(self.run_planner(content, status))


class PlannedPipelineTests(unittest.TestCase):
    def setUp(self):
        m._answer_model_retry_at = 0.0
        self.whatsapp = m.SalomaoSupervisorAgent(session_id="t", user_metadata={"originating_channel": "whatsapp"})

    def run_with(self, plan, results, model=GROUNDED, message="Como cadastro um membro?"):
        with patch.object(m.query_planning, "plan_query", return_value=plan), \
                patch.object(m.knowledge_base, "search", side_effect=results) as search, \
                patch.object(m, "Agent") as agent:
            agent.return_value.run.return_value = model
            result = self.whatsapp.run_pipeline(message=message)
        return result, search, agent

    def test_contract_request_is_not_searched(self):
        result, search, agent = self.run_with(QueryPlan(intent="handoff"), [],
                                              message="Preciso saber como faço para cancelar ou suspender o contrato")
        search.assert_not_called()
        agent.assert_not_called()
        self.assertTrue(result.requires_human_handoff)

    def test_thanks_is_not_searched(self):
        result, search, _ = self.run_with(QueryPlan(intent="closing"), [], message="Obrigado pela atenção")
        search.assert_not_called()
        self.assertEqual(result.answer_status, "closing")
        self.assertNotIn("Fonte", result.message)

    def test_vague_message_gets_the_planned_specific_question(self):
        question = "Você quer conhecer a inChurch para contratar ou já usa a plataforma e tem uma dúvida?"
        result, search, _ = self.run_with(QueryPlan(intent="clarify", clarifying_question=question), [],
                                          message="Gostaria de saber como funciona")
        search.assert_not_called()
        self.assertEqual((result.message, result.answer_status), (question, "clarification"))

    def test_original_and_rewritten_queries_are_merged_by_best_score(self):
        # Rewrite alone ranked Kids first; the original question keeps the right article on top.
        original = [doc("members", "Cadastro de Membros pelo +Novo", 0.741), doc("people", "Pessoas | Como cadastrar um membro?", 0.674)]
        rewritten = [doc("kids", "inChurch KIDS | Como cadastro as crianças?", 0.699), doc("members", "Cadastro de Membros pelo +Novo", 0.657)]
        result, search, agent = self.run_with(QueryPlan(intent="search", query="como cadastrar um membro na plataforma inChurch"),
                                              [original, rewritten])
        self.assertEqual([c.args[0] for c in search.call_args_list],
                         ["Como cadastro um membro?", "como cadastrar um membro na plataforma inChurch"])
        sent = m.json.loads(agent.return_value.run.call_args.args[0])["articles"]
        self.assertEqual(sent[0]["id"], "members")
        self.assertEqual(sent[0]["score"], 0.741)
        self.assertEqual(result.answer_status, "answered")

    def test_only_weak_matches_never_ground_an_answer(self):
        # "agora entrou" -> foto de perfil 0.428: below the gate, no model answer.
        result, _, agent = self.run_with(QueryPlan(intent="search", query="erro de login no aplicativo"),
                                         [[doc("photo", "Pessoas | Como adiciono uma foto?", 0.428)], []])
        agent.assert_not_called()
        self.assertEqual(result.answer_status, "no_match")
        self.assertTrue(result.requires_human_handoff)

    def test_strong_match_after_rewrite_is_answered(self):
        # Shalom report: raw 0.571 (buscador de células), rewritten 0.727 (relatórios pendentes).
        result, _, agent = self.run_with(
            QueryPlan(intent="search", query="como o lider de celula acompanha relatorios pendentes no aplicativo"),
            [[doc("finder", "Células | Buscador de células no site", 0.571)],
             [doc("members", "Células | Como acompanhar e solicitar relatórios pendentes", 0.727)]],
            message="Me enviaram um e-mail informando que estou com 6 relatórios de célula atrasados")
        sent = m.json.loads(agent.return_value.run.call_args.args[0])["articles"]
        # The right article leads; the weaker match stays below it for the answer model.
        self.assertEqual([a["id"] for a in sent], ["members", "finder"])
        self.assertEqual(result.answer_status, "answered")

    def test_planner_failure_keeps_single_direct_search(self):
        result, search, _ = self.run_with(None, [[doc("members", "Cadastro de Membros pelo +Novo", 0.741)]])
        self.assertEqual(search.call_count, 1)
        self.assertEqual(result.answer_status, "answered")

    def test_gate_does_not_apply_to_lexical_catalog_or_unscored_sources(self):
        self.assertTrue(m._relevant_enough(doc("a", "A", 12.5, "published")))
        self.assertTrue(m._relevant_enough({"id": "b", "title": "B"}))
        self.assertTrue(m._relevant_enough(doc("c", "C", 0.2, "previous_source")))
        self.assertFalse(m._relevant_enough(doc("d", "D", 0.44)))
        self.assertTrue(m._relevant_enough(doc("e", "E", 0.45)))


if __name__ == "__main__":
    unittest.main()
