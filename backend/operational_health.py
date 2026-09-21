"""Cached readiness and structured alerts. HTTP probes never perform remote I/O."""
import asyncio
import logging
import time
import uuid

import requests
import config
from api_security import security_configured

logger = logging.getLogger("salomao.health")


def probe_supabase_write():
    """One dedicated sentinel row, overwritten each time; no customer records."""
    nonce = uuid.uuid4().hex
    response = requests.post((config.SUPABASE_URL or "").rstrip("/") + "/rest/v1/salomao_sessions",
        params={"on_conflict": "session_id"}, timeout=(3, 5),
        headers={"apikey": config.SUPABASE_KEY or "", "Authorization": "Bearer " + (config.SUPABASE_KEY or ""),
                 "Prefer": "resolution=merge-duplicates,return=representation"},
        json={"session_id": "whatsapp_salomao_healthcheck", "metadata": {"health_probe": nonce}})
    response.raise_for_status()
    rows = response.json()
    if not rows or rows[0].get("metadata", {}).get("health_probe") != nonce:
        raise RuntimeError("supabase_write_not_confirmed")


class OperationalHealth:
    def __init__(self):
        self.reset(False)

    def reset(self, polling_enabled):
        self.polling_enabled = polling_enabled
        self.last_poll_success = None
        self.poll_failures = 0
        self.checks = {}
        self.checked_at = None
        self.alerts = set()
        self.last_alert = 0

    def polling_result(self, success):
        if success:
            self.last_poll_success = time.monotonic()
            self.poll_failures = 0
        else:
            self.poll_failures += 1

    def snapshot(self):
        reasons = []
        if not security_configured():
            reasons.append("security_not_configured")
        if self.checked_at is None or time.monotonic() - self.checked_at > 90:
            reasons.append("dependency_probe_stale")
        reasons.extend(key for key, value in self.checks.items() if not value)
        if self.polling_enabled:
            if self.last_poll_success is None or time.monotonic() - self.last_poll_success > config.HEALTH_POLLING_MAX_AGE_SECONDS:
                reasons.append("polling_stale")
            if self.poll_failures >= 3:
                reasons.append("polling_failed")
        return {"status": "not_ready" if reasons else "ready", "reasons": sorted(set(reasons)),
                "polling_enabled": self.polling_enabled, "polling_failures": self.poll_failures}

    def report(self):
        current = set(self.snapshot()["reasons"])
        now = time.monotonic()
        for reason in sorted(self.alerts - current):
            logger.info("Dependencia recuperada", extra={"event": "health.recovered", "reason": reason})
        for reason in sorted(current):
            if reason not in self.alerts or now - self.last_alert >= 300:
                logger.error("Servico sem prontidao", extra={"event": "health.alert", "reason": reason})
        if current != self.alerts or now - self.last_alert >= 300:
            self.last_alert = now
        self.alerts = current

    async def run(self, delivery_store, webhook_store):
        while True:
            # Sequential bounded probes avoid accumulating timed-out threads.
            checks = dict(self.checks)
            try:
                state = await asyncio.to_thread(delivery_store.health)
                checks["delivery_store_unwritable"] = True
                checks["deliveries_held"] = state["held"] == 0
                checks["deliveries_stalled"] = state["oldest_age_seconds"] < config.HEALTH_DELIVERY_MAX_AGE_SECONDS
            except Exception:
                checks["delivery_store_unwritable"] = False
            try:
                state = await asyncio.to_thread(webhook_store.health)
                checks["webhook_store_unwritable"] = True
                checks["webhooks_stalled"] = state["oldest_age_seconds"] < config.HEALTH_DELIVERY_MAX_AGE_SECONDS
            except Exception:
                checks["webhook_store_unwritable"] = False
            try:
                await asyncio.to_thread(probe_supabase_write)
                checks["supabase_unwritable"] = True
            except Exception:
                checks["supabase_unwritable"] = False
            self.checks, self.checked_at = checks, time.monotonic()
            self.report()
            await asyncio.sleep(30)
