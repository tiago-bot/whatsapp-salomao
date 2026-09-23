"""Route a verified WhatsApp menu reply before the AI eligibility filter.

The conversation webhook is the primary trigger. Durable watches cover delayed
CRM writes and missed events; CRM search is only additional discovery.
"""
from datetime import datetime, timezone
import logging
import re
import time
import unicodedata

import hubspot_service as hs
from conversation_context import message_time

logger = logging.getLogger("salomao.menu")
WAITING_STAGE = "1113543321"
WATCH_STAGES = {"939271304", WAITING_STAGE, "1115636653", "1115636654"}
ENTRY_POLICY_VERSION = "2026-09-23-menu-entry-v1"


def normalized(text):
    text = unicodedata.normalize("NFKD", str(text or "")).casefold()
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def ai_menu_choice(messages):
    rows = hs.parse_incoming_messages(messages)
    if any(message_time(m.get("created_at")) is None for m in rows):
        return None
    rows.sort(key=lambda m: (message_time(m["created_at"]), m["id"]))
    menu_index = None
    for index, message in enumerate(rows):
        text = normalized(message.get("text")).replace("\ufe0f", "").replace("\u20e3", "")
        if (not message["is_from_visitor"] and message["sender_id"] == hs.SALOMAO_ACTOR_ID
                and re.search(r"2\s*duvidas sobre a plataforma", text)
                and re.search(r"1\s*suporte para a plataforma", text)
                and "boleto" in text and "opcao desejada" in text):
            menu_index = index
    if menu_index is None:
        return None
    following = rows[menu_index + 1:]
    # A human/bot response after the menu takes ownership of the interaction.
    if not following or any(not m["is_from_visitor"] for m in following):
        return None
    choice = following[0]
    text = normalized(choice["text"]).replace("\ufe0f", "").replace("\u20e3", "")
    if (not re.fullmatch(r"(?:opcao\s*)?2[.!]?", text)
            or choice.get("raw", {}).get("attachments")):
        return None
    # A changed selection or explicit request for a human cancels auto-routing.
    from handoff import requests_human
    if any(normalized(m["text"]) in {"1", "3"} or requests_human(m["text"]) for m in following[1:]):
        return None
    if message_time(choice["created_at"]) <= message_time(rows[menu_index]["created_at"]):
        return None
    age = (datetime.now(timezone.utc) - message_time(choice["created_at"])).total_seconds()
    if age < 0 or age > 24 * 3600:
        return None
    return choice


class MenuRouter:
    def __init__(self, store):
        self.store = store
        self.last_discovery = 0.0
        with store._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS menu_watches (
                thread_id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, checked_at REAL NOT NULL DEFAULT 0)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS menu_routes (
                thread_id TEXT NOT NULL, message_id TEXT NOT NULL, ticket_id TEXT NOT NULL,
                routed_at TEXT NOT NULL, PRIMARY KEY(thread_id,message_id))""")

    def watch(self, thread_id, ticket_id):
        with self.store._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO menu_watches(thread_id,ticket_id) VALUES(?,?)",
                         (str(thread_id), str(ticket_id)))

    def forget(self, thread_id):
        with self.store._connect() as conn:
            conn.execute("DELETE FROM menu_watches WHERE thread_id=?", (str(thread_id),))

    @staticmethod
    def eligible(ticket):
        p = (ticket or {}).get("properties", {})
        return (str(p.get("hs_pipeline")) == hs.SALOMAO_PIPELINE
                and str(p.get("hs_pipeline_stage")) == WAITING_STAGE
                and str(p.get("hubspot_owner_id")) == hs.SALOMAO_ACTOR_ID.removeprefix("A-"))

    def process(self, thread_id, ticket_id):
        with self.store.thread_lock(thread_id) as acquired:
            if not acquired:
                return False
            with self.store._connect() as conn:
                conn.execute("UPDATE menu_watches SET checked_at=? WHERE thread_id=?", (time.time(), str(thread_id)))
            ticket = hs.get_ticket_by_id(ticket_id)
            if ticket is None:
                raise hs.HubSpotReadError("menu_ticket_unavailable")
            props = ticket.get("properties", {})
            if (str(props.get("hs_pipeline")) != hs.SALOMAO_PIPELINE
                    or str(props.get("hs_pipeline_stage")) not in WATCH_STAGES
                    or str(props.get("hubspot_owner_id") or "") not in {"", hs.SALOMAO_ACTOR_ID.removeprefix("A-")}):
                self.forget(thread_id)
                return False
            if not self.eligible(ticket):
                return False
            thread = hs.get_thread_by_id(thread_id)
            if not thread:
                raise hs.HubSpotReadError("menu_thread_unavailable")
            if (str(thread.get("associatedTicketId")) != str(ticket_id)
                    or str(thread.get("originalChannelId")) != "1007"
                    or thread.get("status") != "OPEN"
                    or thread.get("assignedTo") not in {None, hs.SALOMAO_ACTOR_ID}):
                self.forget(thread_id)
                return False
            choice = ai_menu_choice(hs.get_thread_messages(thread_id, strict=True))
            if not choice:
                return False
            with self.store._connect() as conn:
                if conn.execute("SELECT 1 FROM menu_routes WHERE thread_id=? AND message_id=?",
                                (str(thread_id), choice["id"])).fetchone():
                    return False
            # Check both CRM ownership and the latest dialogue immediately before
            # the narrow, idempotent stage update. Never change the owner/pipeline.
            fresh = hs.get_ticket_by_id(ticket_id)
            if fresh is None:
                raise hs.HubSpotReadError("menu_ticket_unavailable")
            if not self.eligible(fresh):
                return False
            current = ai_menu_choice(hs.get_thread_messages(thread_id, strict=True))
            if not current or current["id"] != choice["id"]:
                return False
            response = hs.requests.patch(f"{hs.HUBSPOT_API_BASE}/crm/v3/objects/tickets/{ticket_id}",
                headers=hs.get_headers(), json={"properties": {"hs_pipeline_stage": hs.SALOMAO_STATUS}},
                timeout=20)
            if response.status_code != 200:
                raise hs.HubSpotReadError("menu_route_update_failed")
            if str(response.json().get("properties", {}).get("hs_pipeline_stage")) != hs.SALOMAO_STATUS:
                raise hs.HubSpotReadError("menu_route_unconfirmed")
            with self.store._connect() as conn:
                conn.execute("INSERT OR IGNORE INTO menu_routes VALUES(?,?,?,?)",
                    (str(thread_id), choice["id"], str(ticket_id), datetime.now(timezone.utc).isoformat()))
            self.forget(thread_id)
            logger.info("Opcao 2 encaminhada para Atendimento IA", extra={"event": "menu.routed",
                "ticket_id": str(ticket_id), "thread_id": str(thread_id), "message_id": choice["id"],
                "stage_id": hs.SALOMAO_STATUS})
            return True

    def discover(self):
        payload = {"filterGroups": [{"filters": [
            {"propertyName": "hs_pipeline", "operator": "EQ", "value": hs.SALOMAO_PIPELINE},
            {"propertyName": "hs_pipeline_stage", "operator": "EQ", "value": WAITING_STAGE},
            {"propertyName": "hubspot_owner_id", "operator": "EQ", "value": hs.SALOMAO_ACTOR_ID.removeprefix("A-")}
        ]}], "limit": 100}
        cursors = set()
        while True:
            response = hs.requests.post(f"{hs.HUBSPOT_API_BASE}/crm/v3/objects/tickets/search",
                                       headers=hs.get_headers(), json=dict(payload), timeout=20)
            if response.status_code != 200:
                raise hs.HubSpotReadError("menu_discovery_unavailable")
            data = response.json()
            if not isinstance(data.get("results"), list):
                raise hs.HubSpotReadError("menu_discovery_invalid")
            for ticket in data["results"]:
                thread = hs.get_conversation_thread_by_ticket(ticket["id"])
                if thread and thread.get("id"):
                    self.watch(thread["id"], ticket["id"])
            after = (data.get("paging") or {}).get("next", {}).get("after")
            if after is None:
                return
            if not data["results"] or str(after) in cursors:
                raise hs.HubSpotReadError("menu_discovery_pagination_stalled")
            cursors.add(str(after))
            payload["after"] = str(after)
            time.sleep(0.25)

    def recover(self):
        # Empty search results never erase watches learned from real webhooks.
        if time.monotonic() - self.last_discovery >= 60:
            self.last_discovery = time.monotonic()
            try:
                self.discover()
            except Exception:
                logger.exception("Descoberta da triagem adiada; acompanhamentos preservados",
                                 extra={"event": "menu.discovery_failed"})
        with self.store._connect() as conn:
            watches = conn.execute("SELECT * FROM menu_watches WHERE checked_at<? ORDER BY checked_at LIMIT 100",
                                   (time.time() - 10,)).fetchall()
        routed = []
        for row in watches:
            try:
                if self.process(row["thread_id"], row["ticket_id"]):
                    routed.append(row["ticket_id"])
            except Exception:
                logger.exception("Escolha do menu sera reavaliada", extra={"event": "menu.retry",
                                 "ticket_id": row["ticket_id"], "thread_id": row["thread_id"]})
        return routed
