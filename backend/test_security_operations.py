"""Security boundaries, concurrency and operational failures; no external I/O."""
import asyncio
import base64
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi.testclient import TestClient
import config
import api_security
import hubspot_bot as bot
import hubspot_service as service
import main_hubspot as api
import operational_health as monitoring
from delivery_store import DeliveryStore
from webhook_store import WebhookStore
from scope_policy import SCOPE_POLICY_VERSION, approval_digest

TOKEN = "offline-admin-token-" + "x" * 32
SECRET = "offline-client-secret"
URL = "https://bot.example/webhook/hubspot"


def signed_request(payload, *, stamp=None, url=URL, raw=None):
    raw = json.dumps(payload, ensure_ascii=False).encode() if raw is None else raw
    stamp = str(int(time.time() * 1000) if stamp is None else stamp)
    source = ("POST" + api_security.signature_uri(url)).encode() + raw + stamp.encode()
    return {"content": raw, "headers": {"Content-Type": "application/json",
        "X-HubSpot-Request-Timestamp": stamp,
        "X-HubSpot-Signature-v3": base64.b64encode(hmac.digest(SECRET.encode(), source, "sha256")).decode()}}


class ApiSecurityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = WebhookStore(str(Path(folder.name) / "webhooks.sqlite3"))
        for obj, name, value in [(config, "ADMIN_API_TOKEN", TOKEN), (config, "HUBSPOT_CLIENT_SECRET", SECRET),
                (config, "HUBSPOT_WEBHOOK_URL", URL), (config, "HUBSPOT_WEBHOOK_ALLOW_V1", False),
                (api, "webhook_store", self.store)]:
            patched = patch.object(obj, name, value)
            patched.start()
            self.addCleanup(patched.stop)
        self.client = TestClient(api.app)
        self.event = {"subscriptionType": "conversation.newMessage", "objectId": 123,
                      "eventId": 456, "occurredAt": int(time.time() * 1000), "attemptNumber": 0}

    def test_every_administrative_route_requires_bearer(self):
        paths = [("GET", "/tickets"), ("GET", "/config"), ("GET", "/thread/1"),
                 ("GET", "/thread/1/messages"), ("GET", "/ticket/1/thread"), ("GET", "/session/1"),
                 ("DELETE", "/session/1"), ("POST", "/ticket/1/transfer-to-human"),
                 ("POST", "/process/ticket"), ("POST", "/process/all"),
                 ("POST", "/thread/1/process"), ("POST", "/test/chat?message=oi"), ("GET", "/admin/health")]
        with patch.object(api, "get_thread_messages") as read, patch.object(api.salomao, "process_message") as generate:
            for method, path in paths:
                for headers in [{}, {"Authorization": "Bearer wrong"}]:
                    with self.subTest(path=path, headers=bool(headers)):
                        self.assertEqual(self.client.request(method, path, headers=headers, json={"ticket_id": "1"}).status_code, 401)
            read.assert_not_called()
            generate.assert_not_called()

    def test_missing_or_short_configuration_fails_closed(self):
        for token in ["", "short"]:
            with patch.object(config, "ADMIN_API_TOKEN", token):
                self.assertEqual(self.client.get("/session/1").status_code, 503)
        with patch.object(config, "HUBSPOT_CLIENT_SECRET", ""):
            self.assertEqual(self.client.post("/webhook/hubspot", **signed_request([self.event])).status_code, 503)

    def test_authorized_access_and_public_liveness(self):
        self.assertEqual(self.client.get("/config", headers={"Authorization": "Bearer " + TOKEN}).status_code, 200)
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/docs").status_code, 404)
        self.assertEqual(self.client.get("/openapi.json").status_code, 404)
        self.assertNotIn("reasons", self.client.get("/ready").json())

    def test_webhook_requires_signature_even_with_admin_token(self):
        response = self.client.post("/webhook/hubspot", json=[self.event], headers={"Authorization": "Bearer " + TOKEN})
        self.assertEqual(response.status_code, 401)
        self.assertIsNone(self.store.next_event())

    def test_authenticated_batch_is_durable_and_replays_are_noops(self):
        response = self.client.post("/webhook/hubspot", **signed_request([self.event]))
        self.assertEqual(response.status_code, 202)
        restarted = WebhookStore(self.store.path)
        self.assertEqual(json.loads(restarted.next_event()["payload"])["objectId"], 123)
        # Signature and body change on HubSpot retry, but the event does not.
        response = self.client.post("/webhook/hubspot", **signed_request([{**self.event, "attemptNumber": 2}]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["accepted"], 0)
        self.assertEqual(restarted.health()["pending"], 1)
        restarted.finish(restarted.next_event()["fingerprint"], success=True)
        self.assertEqual(self.client.post("/webhook/hubspot", **signed_request([self.event])).json()["accepted"], 0)

    def test_tampering_expired_future_and_invalid_timestamps_rejected(self):
        request = signed_request([self.event])
        request["content"] += b" "
        self.assertEqual(self.client.post("/webhook/hubspot", **request).status_code, 401)
        for stamp in [0, int(time.time() * 1000) - 301000, int(time.time() * 1000) + 301000, "bad"]:
            self.assertEqual(self.client.post("/webhook/hubspot", **signed_request([self.event], stamp=stamp)).status_code, 401)

    def test_validation_errors_are_http_errors_and_batch_is_atomic(self):
        self.assertEqual(self.client.post("/webhook/hubspot", **signed_request(None, raw=b"not json")).status_code, 400)
        for payload in [None, [], [self.event, None], [{"objectId": 1}], [{**self.event, "objectId": {}}]]:
            self.assertEqual(self.client.post("/webhook/hubspot", **signed_request(payload)).status_code, 422)
        self.assertIsNone(self.store.next_event())
        request = signed_request(None, raw=b"x" * (api_security.MAX_WEBHOOK_BYTES + 1))
        self.assertEqual(self.client.post("/webhook/hubspot", **request).status_code, 413)

    def test_queue_failure_is_503_and_can_be_retried(self):
        with patch.object(self.store, "accept", side_effect=sqlite3.OperationalError):
            self.assertEqual(self.client.post("/webhook/hubspot", **signed_request([self.event])).status_code, 503)
        self.assertEqual(self.client.post("/webhook/hubspot", **signed_request([self.event])).status_code, 202)

    def test_concurrent_replays_have_one_durable_winner(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            accepted = list(executor.map(lambda _: self.store.accept([self.event]), range(8)))
        self.assertEqual(sum(accepted), 1)
        self.assertEqual(self.store.health()["pending"], 1)

    def test_proxy_headers_cannot_change_signed_url_and_selective_decoding(self):
        url = URL + "?next=%2Ffoo%3Fq%3Done%26two&name=%C3%A1"
        with patch.object(config, "HUBSPOT_WEBHOOK_URL", url):
            request = signed_request([self.event], url=url)
            request["headers"].update({"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "http"})
            self.assertEqual(self.client.post(url, **request).status_code, 202)
            self.assertEqual(self.client.post("/webhook/hubspot", **request).status_code, 401)
        self.assertEqual(api_security.signature_uri("%2f%3f%26%3d%25"), "/?%26%3d%25")

    def test_v1_requires_opt_in_signed_freshness_and_never_downgrades_v3(self):
        raw = json.dumps([self.event]).encode()
        headers = {"X-HubSpot-Signature-Version": "v1", "X-HubSpot-Signature": hashlib.sha256(SECRET.encode() + raw).hexdigest()}
        self.assertEqual(self.client.post("/webhook/hubspot", content=raw, headers=headers).status_code, 401)
        with patch.object(config, "HUBSPOT_WEBHOOK_ALLOW_V1", True):
            self.assertEqual(self.client.post("/webhook/hubspot", content=raw, headers=headers).status_code, 202)
            headers["X-HubSpot-Signature-v3"] = "invalid"
            self.assertEqual(self.client.post("/webhook/hubspot", content=raw, headers=headers).status_code, 401)
            stale = json.dumps([{**self.event, "occurredAt": 1}]).encode()
            headers = {"X-HubSpot-Signature-Version": "v1", "X-HubSpot-Signature": hashlib.sha256(SECRET.encode() + stale).hexdigest()}
            self.assertEqual(self.client.post("/webhook/hubspot", content=stale, headers=headers).status_code, 401)

    def test_worker_retains_failures_and_finishes_successes(self):
        async def scenario():
            self.store.accept([self.event])
            with patch.object(api, "dispatch_webhook_event", new_callable=AsyncMock, side_effect=RuntimeError) as dispatch:
                task = asyncio.create_task(api.webhook_loop())
                for _ in range(100):
                    if dispatch.await_count and self.store.next_event() is None:
                        break
                    await asyncio.sleep(.01)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self.assertEqual(dispatch.await_count, 1)
                self.assertEqual(self.store.health()["pending"], 1)
            with self.store.connect() as conn:
                conn.execute("UPDATE webhook_events SET available_at=0")
            with patch.object(api, "dispatch_webhook_event", new_callable=AsyncMock):
                task = asyncio.create_task(api.webhook_loop())
                for _ in range(100):
                    if self.store.health()["pending"] == 0:
                        break
                    await asyncio.sleep(.01)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self.assertEqual(self.store.health()["pending"], 0)
        asyncio.run(scenario())

    def test_slow_sync_route_does_not_block_health(self):
        async def scenario(path, target, result, method="GET"):
            entered, release = threading.Event(), threading.Event()
            def slow(*args, **kwargs):
                entered.set()
                if not release.wait(2):
                    raise AssertionError("event loop blocked by synchronous route")
                return result
            with patch.object(target[0], target[1], side_effect=slow):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as client:
                    request = asyncio.create_task(client.request(method, path, headers={"Authorization": "Bearer " + TOKEN}))
                    try:
                        for _ in range(100):
                            if entered.is_set():
                                break
                            await asyncio.sleep(.005)
                        self.assertTrue(entered.is_set())
                        response = await asyncio.wait_for(client.get("/health"), .5)
                        self.assertEqual(response.status_code, 200)
                    finally:
                        release.set()
                    self.assertEqual((await request).status_code, 200)
        for args in [("/tickets", (api, "get_tickets_for_salomao"), []),
                     ("/session/1", (api.salomao, "get_conversation_history"), []),
                     ("/session/1", (api.salomao, "clear_conversation"), None, "DELETE"),
                     ("/ticket/1/transfer-to-human", (api, "transfer_to_human"), True, "POST")]:
            asyncio.run(scenario(*args))


class PaginationTests(unittest.TestCase):
    @staticmethod
    def page(ids, after=None, status=200):
        return MagicMock(status_code=status, json=MagicMock(return_value={"results": [{"id": str(i)} for i in ids],
            **({"paging": {"next": {"after": after}}} if after is not None else {})}))

    def test_all_pages_deduplicated_with_filters_preserved(self):
        with patch.object(service.requests, "post", side_effect=[self.page(range(100), "100"), self.page(range(99, 150), "200"), self.page([150])]) as post:
            rows = service.get_tickets_for_salomao(strict=True)
        self.assertEqual(len(rows), 151)
        bodies = [c.kwargs["json"] for c in post.call_args_list]
        self.assertNotIn("after", bodies[0])
        self.assertEqual([p.get("after") for p in bodies], [None, "100", "200"])
        self.assertTrue(all(p["filterGroups"] == bodies[0]["filterGroups"] for p in bodies))

    def test_partial_failure_and_repeated_cursor_never_return_partial_success(self):
        for second in [self.page([], status=503), self.page([2], "100"), self.page([], "200")]:
            with patch.object(service.requests, "post", side_effect=[self.page([1], "100"), second]):
                with self.assertRaises(service.HubSpotReadError):
                    service.get_tickets_for_salomao(strict=True)


class TakeoverTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = DeliveryStore(Path(folder.name) / "delivery.sqlite3")
        self.bot = bot.HubSpotSalomaoBot(self.store, MagicMock(), debounce_seconds=0)
        self.ticket = {"properties": {"hs_pipeline": service.SALOMAO_PIPELINE, "hs_pipeline_stage": service.SALOMAO_STATUS,
                                      "hubspot_owner_id": service.SALOMAO_ACTOR_ID.removeprefix("A-")}}
        self.human = {"properties": {**self.ticket["properties"], "hubspot_owner_id": "human"}}

    def entry(self, parts, transfer=False):
        response = "\n".join(parts)
        return self.store.enqueue("t", "m", {"parts": parts, "response": response, "transfer_requested": transfer,
            "scope_policy_version": SCOPE_POLICY_VERSION, "scope_digest": approval_digest(response, parts)})

    def test_human_takes_over_between_parts_and_draft_never_resumes(self):
        entry = self.entry(["one", "two"], True)
        with patch.object(bot, "get_ticket_by_id", side_effect=[self.ticket, self.human]), \
             patch.object(bot, "reply_to_visitor", return_value={"id": "first"}) as send, \
             patch.object(bot, "transfer_ticket_to_human_support") as transfer, patch.object(bot, "create_ticket_handoff_note") as note:
            self.assertEqual(self.bot._deliver(entry, "ticket")["error"], "eligibility_changed")
            send.assert_called_once_with("t", "one")
            transfer.assert_not_called()
            note.assert_not_called()
        with patch.object(bot, "get_ticket_by_id", return_value=self.ticket), patch.object(bot, "reply_to_visitor") as send:
            self.bot._deliver(entry, "ticket")
            send.assert_not_called()
        self.assertEqual(self.store.get("t", "m")["sent_parts"], 1)

    def test_takeover_during_note_blocks_transfer(self):
        entry = self.entry(["one"], True)
        with patch.object(bot, "get_ticket_by_id", side_effect=[self.ticket, self.ticket, self.human]), \
             patch.object(bot, "reply_to_visitor", return_value={"id": "first"}), \
             patch.object(bot, "create_ticket_handoff_note", return_value={"id": "note"}), \
             patch.object(bot, "transfer_ticket_to_human_support") as transfer:
            self.assertEqual(self.bot._deliver(entry, "ticket")["error"], "eligibility_changed")
            transfer.assert_not_called()
        self.assertEqual(self.store.get("t", "m")["handoff_note_id"], "note")

    def test_unknown_owner_defers_without_losing_pending_delivery(self):
        entry = self.entry(["one"])
        with patch.object(bot, "get_ticket_by_id", return_value=None), patch.object(bot, "reply_to_visitor") as send:
            self.assertEqual(self.bot._deliver(entry, "ticket")["error"], "ticket_unavailable")
            send.assert_not_called()
        self.assertFalse(self.store.get("t", "m")["complete"])

    def test_outbox_probe_detects_held_stalled_and_write_failure(self):
        self.entry(["one"])
        self.store.begin_part("t", "m", 0)
        self.store.failed_part("t", "m", 0)
        with self.store._connect() as conn:
            conn.execute("UPDATE deliveries SET queued_at=?", (time.time() - 400,))
        state = self.store.health()
        self.assertEqual(state["held"], 1)
        self.assertGreater(state["oldest_age_seconds"], 300)
        with self.store._connect() as conn:
            conn.execute("CREATE TRIGGER no_probe BEFORE INSERT ON health_probe BEGIN SELECT RAISE(ABORT,'read only'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.health()


class HealthTests(unittest.TestCase):
    def test_readiness_http_status_reflects_state_without_leaking_details(self):
        health = monitoring.OperationalHealth()
        with patch.object(api, "health", health), patch.object(monitoring, "security_configured", return_value=True):
            client = TestClient(api.app)
            self.assertEqual(client.get("/ready").status_code, 503)
            health.checked_at = time.monotonic()
            health.checks = {"supabase_unwritable": True}
            self.assertEqual(client.get("/ready").json(), {"status": "ready"})
            self.assertEqual(client.get("/ready").status_code, 200)
            health.checks["supabase_unwritable"] = False
            self.assertEqual(client.get("/ready").status_code, 503)
            self.assertEqual(client.get("/health").status_code, 200)

    def test_polling_counts_read_and_processing_failures_but_not_takeovers(self):
        async def scenario(tickets, result, success):
            health = monitoring.OperationalHealth()
            health.reset(True)
            async def stop_after_cycle(_):
                api.polling_active = False
            kwargs = {"side_effect": tickets} if isinstance(tickets, Exception) else {"return_value": tickets}
            with patch.object(api, "health", health), patch.object(api, "polling_active", True), \
                 patch.object(api, "get_tickets_for_salomao", **kwargs) as fetch, \
                 patch.object(api, "process_single_ticket", return_value=result), \
                 patch.object(api.asyncio, "sleep", side_effect=stop_after_cycle):
                await api.polling_loop()
            fetch.assert_called_once_with(strict=True)
            self.assertEqual(health.last_poll_success is not None, success)
            self.assertEqual(health.poll_failures, 0 if success else 1)
        for args in [([], {}, True), (service.HubSpotReadError(), {}, False),
                     ([{"id": "1"}], {"success": False, "reason": "different_hubspot_owner_id"}, True),
                     ([{"id": "1"}], {"success": False, "reason": "ticket_unavailable"}, False),
                     ([{"id": "1"}], {"success": False, "error": "delivery_uncertain"}, False)]:
            asyncio.run(scenario(*args))

    def test_polling_failure_staleness_recovery_and_disabled_mode(self):
        health = monitoring.OperationalHealth()
        health.reset(True)
        health.checks = {"supabase_unwritable": True}
        health.checked_at = time.monotonic()
        with patch.object(monitoring, "security_configured", return_value=True):
            self.assertIn("polling_stale", health.snapshot()["reasons"])
            health.polling_result(True)
            self.assertEqual(health.snapshot()["status"], "ready")
            for _ in range(3):
                health.polling_result(False)
            self.assertIn("polling_failed", health.snapshot()["reasons"])
            with self.assertLogs("salomao.health", level="ERROR") as logs:
                health.report()
            self.assertTrue(logs.output)
            health.polling_result(True)
            with self.assertLogs("salomao.health", level="INFO"):
                health.report()
            health.polling_enabled = False
            health.last_poll_success = None
            self.assertEqual(health.snapshot()["status"], "ready")
            health.checked_at -= 91
            self.assertIn("dependency_probe_stale", health.snapshot()["reasons"])

    def test_background_probes_report_failures_and_recover(self):
        async def once(health, store, inbox):
            task = asyncio.create_task(health.run(store, inbox))
            for _ in range(100):
                if health.checked_at is not None:
                    break
                await asyncio.sleep(.005)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        health = monitoring.OperationalHealth()
        store, inbox = MagicMock(), MagicMock()
        store.health.return_value = {"held": 1, "oldest_age_seconds": 400}
        inbox.health.return_value = {"oldest_age_seconds": 400}
        with patch.object(monitoring, "security_configured", return_value=True), \
             patch.object(monitoring, "probe_supabase_write", side_effect=RuntimeError):
            asyncio.run(once(health, store, inbox))
        self.assertTrue({"deliveries_held", "deliveries_stalled", "webhooks_stalled", "supabase_unwritable"} <= set(health.snapshot()["reasons"]))
        store.health.side_effect = sqlite3.OperationalError
        health.checked_at = None
        with patch.object(monitoring, "probe_supabase_write"):
            asyncio.run(once(health, store, inbox))
        self.assertIn("delivery_store_unwritable", health.snapshot()["reasons"])

    def test_supabase_probe_requires_confirmed_write(self):
        response = MagicMock()
        response.json.return_value = []
        with patch.object(monitoring.requests, "post", return_value=response) as post:
            with self.assertRaisesRegex(RuntimeError, "not_confirmed"):
                monitoring.probe_supabase_write()
        self.assertEqual(post.call_args.kwargs["json"]["session_id"], "whatsapp_salomao_healthcheck")
        self.assertIn("timeout", post.call_args.kwargs)
