import asyncio
import io
import json
import logging
import subprocess
import sys
import unittest
from unittest.mock import patch

from logging_config import JsonFormatter, log_context


class LoggingTests(unittest.TestCase):
    def test_unchanged_polling_logs_one_summary_not_every_cycle(self):
        import main_hubspot as api
        cycles = []
        async def tick(_):
            cycles.append(True)
            if len(cycles) == 3:
                api.polling_active = False
        with patch.object(api, "polling_active", True), patch.object(api, "get_tickets_for_salomao", return_value=[]) as search, patch.object(api.asyncio, "sleep", side_effect=tick), patch.object(api.time, "monotonic", return_value=10), self.assertLogs(api.logger, level="INFO") as logs:
            asyncio.run(api.polling_loop())
        self.assertEqual(search.call_count, 3)
        self.assertEqual(sum(getattr(record, "event", None) == "polling.summary" for record in logs.records), 1)
        self.assertTrue(all(call.kwargs.get("strict") for call in search.call_args_list))

    def test_json_levels_emojis_and_secrets(self):
        formatter = JsonFormatter()
        record = logging.LogRecord("salomao", logging.INFO, __file__, 1,
            "✅ token=pat-na1-secret sk-test-secret pcsk_secret contato@example.com", (), None)
        data = json.loads(formatter.format(record))
        self.assertEqual(data["level"], "info")
        self.assertNotIn("✅", data["message"])
        self.assertNotIn("secret", data["message"])
        self.assertNotIn("contato@example.com", data["message"])

    def test_correlation_survives_to_thread_and_resets_between_conversations(self):
        formatter = JsonFormatter()
        record = logging.LogRecord("salomao", logging.INFO, __file__, 1, "ok", (), None)
        async def run():
            with log_context(ticket_id="ticket", thread_id="thread", message_id="message"):
                return await asyncio.to_thread(lambda: json.loads(formatter.format(record)))
        self.assertEqual(asyncio.run(run())["message_id"], "message")
        self.assertNotIn("message_id", json.loads(formatter.format(record)))

    def test_exception_omits_raw_exception_body(self):
        try:
            raise ValueError("customer private text and secret")
        except ValueError:
            record = logging.LogRecord("salomao", logging.ERROR, __file__, 1, "Falha", (), sys.exc_info())
        output = JsonFormatter().format(record)
        self.assertNotIn("customer private", output)
        self.assertEqual(json.loads(output)["error_type"], "ValueError")
        self.assertIn("stack", json.loads(output))

    def test_application_and_uvicorn_use_stdout_with_real_severity(self):
        code = '''import logging
from logging_config import configure_logging
logging.getLogger("uvicorn.error").addHandler(logging.StreamHandler())
configure_logging()
configure_logging()
logging.info("normal")
logging.getLogger("uvicorn.error").info("server started")
logging.getLogger("httpx2").info("noisy request")
logging.warning("attention")
logging.error("failure")
'''
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        self.assertEqual(result.stderr, "")
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([r["level"] for r in rows], ["info", "info", "warn", "error"])
