"""API contract tests with all external work mocked."""
import os
import unittest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

os.environ["HUBSPOT_POLLING_ENABLED"] = "false"
import main
import main_hubspot
import hubspot_service
import salomao_agent


class ApiTests(unittest.TestCase):
    def test_entry_date_property_triggers_ticket_processing(self):
        for date in ["1788368227429", "2026-09-02T19:37:07.429Z"]:
            with self.subTest(date=date), patch.object(main_hubspot, "process_ticket_if_valid", new_callable=AsyncMock) as process:
                response = TestClient(main_hubspot.app).post("/webhook/hubspot", json=[{
                    "subscriptionType": "ticket.propertyChange", "objectId": 123,
                    "propertyName": "hs_v2_date_entered_1269308450", "propertyValue": date,
                }])
                self.assertEqual(response.status_code, 200)
                process.assert_awaited_once_with("123")

    def test_other_properties_and_cleared_entry_dates_do_not_trigger(self):
        cases = [("hs_v2_date_entered_999", "1788368227429"),
                 ("hs_pipeline_stage", "1269308450"),
                 ("hs_v2_date_entered_1269308450", ""),
                 ("hs_v2_date_entered_1269308450", None)]
        for name, value in cases:
            with self.subTest(name=name, value=value), patch.object(main_hubspot, "process_ticket_if_valid", new_callable=AsyncMock) as process:
                TestClient(main_hubspot.app).post("/webhook/hubspot", json={
                    "subscriptionType": "ticket.propertyChange", "objectId": 123,
                    "propertyName": name, "propertyValue": value,
                })
                process.assert_not_awaited()

    def test_entry_webhook_still_checks_all_ticket_filters(self):
        props = {"hs_pipeline": "636594474", "hs_pipeline_stage": "1269308450", "hubspot_owner_id": "81908844"}
        for changed in [None, "hs_pipeline", "hs_pipeline_stage", "hubspot_owner_id"]:
            current = {**props}
            if changed:
                current[changed] = "another-value"
            with self.subTest(changed=changed), patch.object(main_hubspot, "get_ticket_by_id", return_value={"properties": current}), patch.object(main_hubspot, "process_single_ticket", return_value={}) as process:
                TestClient(main_hubspot.app).post("/webhook/hubspot", json={
                    "subscriptionType": "ticket.propertyChange", "objectId": 123,
                    "propertyName": "hs_v2_date_entered_1269308450", "propertyValue": "1788368227429",
                })
                if changed:
                    process.assert_not_called()
                else:
                    process.assert_called_once_with("123")

    def test_subscription_and_backend_use_same_entry_property(self):
        import json
        from pathlib import Path
        manifest = Path(__file__).resolve().parents[1] / "hubspot-app/src/app/webhooks/webhooks-hsmeta.json"
        config = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(config["config"]["subscriptions"]["legacyCrmObjects"][0]["propertyName"], hubspot_service.SALOMAO_ENTRY_PROPERTY)
        self.assertEqual(hubspot_service.SALOMAO_PIPELINE, "636594474")
        self.assertEqual(hubspot_service.SALOMAO_STATUS, "1269308450")

    def test_chat_preserves_grounded_contract(self):
        response = {"success": True, "response": "Orientação", "session_id": "offline",
                    "answer_status": "documentation", "sources": [{"title": "Ajuda", "url": "https://portal.inchurch.com.br/pt-br"}], "suggested_actions": []}
        with patch.object(main.salomao, "process_message", return_value=response):
            result = TestClient(main.app).post("/chat", json={"message": "estorno", "session_id": "offline"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["sources"], response["sources"])
        self.assertEqual(result.json()["answer_status"], "documentation")

    def test_hubspot_startup_with_polling_disabled(self):
        with patch.object(main_hubspot, "HUBSPOT_POLLING_ENABLED", False), patch.object(main_hubspot, "get_tickets_for_salomao") as tickets:
            with TestClient(main_hubspot.app) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                tickets.assert_not_called()

    def test_test_chat_uses_whatsapp_channel_without_sending(self):
        with patch.object(main_hubspot.salomao, "process_message", return_value={"success": True, "response": "*Olá*"}) as agent, patch.object(main_hubspot, "reply_to_visitor") as send:
            result = TestClient(main_hubspot.app).post("/test/chat", params={"message": "oi", "session_id": "offline"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(agent.call_args.kwargs["originating_channel"], "whatsapp")
        send.assert_not_called()

    def test_hubspot_payload_is_safe_and_formatted(self):
        with patch.object(hubspot_service.requests, "post") as post:
            post.return_value.status_code = 201
            post.return_value.json.return_value = {"id": "sent"}
            hubspot_service.send_message_to_thread("thread", "## Título\n\n**Eventos** <img src=x>", "1003", "account", "actor", [])
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["text"], "*Título*\n\n*Eventos* <img src=x>")
        self.assertNotIn("<img", payload["richText"])

    def test_invalid_audio_never_reaches_knowledge_search(self):
        agent = object.__new__(salomao_agent.SalomaoAgent)
        with patch.object(salomao_agent, "SalomaoSupervisorAgent") as supervisor:
            result = agent.process_message("", audio_base64="invalid!", session_id="offline")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "audio_unavailable")
        supervisor.assert_not_called()

    def test_real_agent_construction_is_compatible_with_installed_agno(self):
        agent = salomao_agent.SalomaoSupervisorAgent(session_id="offline", user_metadata={"originating_channel": "whatsapp"})
        self.assertIsNotNone(agent.team)


if __name__ == "__main__":
    unittest.main()
