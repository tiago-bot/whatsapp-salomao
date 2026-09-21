"""Verify an existing HubSpot receipt and reconcile it locally. Never POSTs.

Run in the worker container, against the same persistent DB and lock directory.
The default is a preview; --apply records receipt, progress, context and audit in
one transaction. No force/retry option: lack of evidence cannot authorize a send.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote

import requests

from config import DELIVERY_DB_PATH
from conversation_context import message_time
from delivery_store import DeliveryStore
from hubspot_service import HUBSPOT_API_BASE, SALOMAO_ACTOR_ID, get_headers
from scope_policy import approved_delivery


def _read(path, params=None):
    try:
        response = requests.get(HUBSPOT_API_BASE + path, headers=get_headers(),
                                params=params, timeout=30, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError("receipt_read_failed")
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("receipt_read_failed")
        return result
    except (requests.RequestException, ValueError):
        # Do not print response bodies, auth headers or exception URLs.
        raise ValueError("receipt_read_failed") from None


def fetch_receipt(thread_id, remote_id, kind, ticket_id):
    thread_path = "/conversations/v3/conversations/threads/" + quote(str(thread_id), safe="")
    if kind == "part":
        return _read(thread_path + "/messages/" + quote(str(remote_id), safe=""))
    thread = _read(thread_path, {"association": "TICKET"})
    associated = (thread.get("threadAssociations") or {}).get("associatedTicketId")
    if not ticket_id or str(associated) != str(ticket_id):
        raise ValueError("ticket_thread_mismatch")
    return _read("/crm/v3/objects/notes/" + quote(str(remote_id), safe=""),
                 {"properties": "hs_note_body,hs_timestamp", "associations": "tickets"})


def reconcile(store, *, thread_id, message_id, remote_id, operator, reason,
              part=None, ticket_id=None, apply=False):
    """part is zero-based internally; None selects the handoff note."""
    thread_id, message_id, remote_id = str(thread_id), str(message_id), str(remote_id)
    if not all(value.strip() for value in (thread_id, message_id, remote_id, operator, reason)):
        raise ValueError("reconciliation_fields_required")
    kind, position = ("note", -1) if part is None else ("part", part)
    with store.thread_lock(thread_id) as acquired:
        if not acquired:
            raise ValueError("conversation_busy")
        receipt = fetch_receipt(thread_id, remote_id, kind, ticket_id)
        if str(receipt.get("id", "")) != remote_id or receipt.get("archived"):
            raise ValueError("receipt_id_mismatch_or_archived")
        with store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
            row = conn.execute("SELECT * FROM deliveries WHERE thread_id=? AND message_id=?",
                               (thread_id, message_id)).fetchone()
            if not row:
                raise ValueError("delivery_not_found")
            payload = json.loads(row["payload"])
            has_audit = conn.execute("SELECT 1 FROM sqlite_master WHERE name='delivery_reconciliations'").fetchone()
            previous = conn.execute("""SELECT id,remote_id FROM delivery_reconciliations
                WHERE thread_id=? AND message_id=? AND kind=? AND part=?""",
                (thread_id, message_id, kind, position)).fetchone() if has_audit else None
            if previous:
                if previous["remote_id"] != remote_id:
                    raise ValueError("receipt_conflicts_with_previous_reconciliation")
                return {"status": "already_reconciled", "audit_id": previous["id"], "remote_id": remote_id}
            if row["complete"] or payload.get("blocked_reason") or not approved_delivery(payload):
                raise ValueError("delivery_not_reconcilable")
            timestamp = message_time(receipt.get("createdAt"))
            if timestamp is None:
                raise ValueError("receipt_timestamp_required")
            if kind == "part":
                if (type(part) is not int or part < 0 or part >= len(payload["parts"])
                        or part != row["sent_parts"]):
                    raise ValueError("part_out_of_order")
                attempt = conn.execute("""SELECT * FROM delivery_attempts
                    WHERE thread_id=? AND message_id=? AND part=?""", (thread_id, message_id, part)).fetchone()
                if not attempt or attempt["state"] not in {"sending", "uncertain"}:
                    raise ValueError("attempt_not_uncertain")
                content = payload["parts"][part]
                if (receipt.get("type") != "MESSAGE" or receipt.get("direction") != "OUTGOING"
                        or receipt.get("text") != content
                        or SALOMAO_ACTOR_ID not in {sender.get("actorId") for sender in receipt.get("senders", [])}
                        or str(receipt.get("threadId", thread_id)) != thread_id
                        or (receipt.get("status") or {}).get("statusType") == "FAILED"):
                    raise ValueError("receipt_does_not_match_delivery")
                attempted_at = message_time(attempt["attempted_at"])
                if attempted_at is None or timestamp < attempted_at - timedelta(seconds=5):
                    raise ValueError("receipt_predates_attempt")
                reused = conn.execute("SELECT 1 FROM delivery_attempts WHERE remote_id=?", (remote_id,)).fetchone()
                previous_state = attempt["state"]
                next_parts = part + 1
            else:
                if (not payload.get("transfer_requested") or not payload.get("handoff_note_body")
                        or row["sent_parts"] != len(payload["parts"])
                        or row["handoff_note_state"] not in {"sending", "uncertain"}):
                    raise ValueError("note_not_uncertain")
                content = payload["handoff_note_body"][:65536]
                tickets = (receipt.get("associations") or {}).get("tickets", {}).get("results", [])
                if (receipt.get("properties", {}).get("hs_note_body") != content
                        or not ticket_id or str(ticket_id) not in {str(t.get("id")) for t in tickets}):
                    raise ValueError("receipt_does_not_match_note")
                reused = conn.execute("SELECT 1 FROM deliveries WHERE handoff_note_id=?", (remote_id,)).fetchone()
                previous_state = row["handoff_note_state"]
                next_parts = row["sent_parts"]
            if reused:
                raise ValueError("receipt_already_used")
            complete = next_parts == len(payload["parts"]) and not payload.get("transfer_requested")
            evidence = {"source": "hubspot_get", "created_at": timestamp.isoformat(),
                        "content_sha256": hashlib.sha256(content.encode()).hexdigest(), "ticket_id": ticket_id}
            result = {"status": "applied" if apply else "preview", "thread_id": thread_id,
                      "message_id": message_id, "kind": kind, "part": part + 1 if part is not None else None,
                      "remote_id": remote_id, "previous_state": previous_state,
                      "sent_parts": next_parts, "complete": complete,
                      "next_action": "pending_inputs" if complete else "resume_remaining_delivery"}
            if not apply:
                return result
            if not has_audit:
                raise ValueError("reconciliation_schema_missing_start_updated_worker_first")
            audit = conn.execute("""INSERT INTO delivery_reconciliations
                (thread_id,message_id,kind,part,remote_id,operator,reason,reconciled_at,previous_state,evidence)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (thread_id, message_id, kind, position, remote_id,
                operator.strip(), reason.strip(), datetime.now(timezone.utc).isoformat(), previous_state,
                json.dumps(evidence, ensure_ascii=False)))
            # A legacy hold may be reconciled before the first updated poll.
            # Preserve its intake boundary before marking the output complete.
            boundary = conn.execute("""SELECT MIN(attempted_at) FROM delivery_attempts
                WHERE thread_id=? AND message_id=?""", (thread_id, message_id)).fetchone()[0]
            if boundary:
                conn.execute("INSERT OR IGNORE INTO intake_checkpoints VALUES(?,?)", (thread_id, boundary))
            if kind == "part":
                conn.execute("""UPDATE delivery_attempts SET state='confirmed',remote_id=?
                    WHERE thread_id=? AND message_id=? AND part=?""", (remote_id, thread_id, message_id, part))
                conn.execute("UPDATE deliveries SET sent_parts=?,complete=? WHERE thread_id=? AND message_id=?",
                             (next_parts, int(complete), thread_id, message_id))
                conn.execute("INSERT OR IGNORE INTO conversation_messages VALUES(?,?,?,?,?)",
                             (thread_id, remote_id, timestamp.isoformat(), "assistant", content))
            else:
                conn.execute("""UPDATE deliveries SET handoff_note_state='confirmed',handoff_note_id=?
                    WHERE thread_id=? AND message_id=?""", (remote_id, thread_id, message_id))
            return {**result, "audit_id": audit.lastrowid}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DELIVERY_DB_PATH)
    parser.add_argument("--thread", required=True)
    parser.add_argument("--message", required=True, help="ID da entrada local, não do recibo")
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--part", type=int, help="Parte da resposta, começando em 1")
    choice.add_argument("--note", action="store_true", help="Reconciliar observação de transferência")
    parser.add_argument("--ticket", help="Obrigatório para --note")
    parser.add_argument("--remote-id", required=True)
    parser.add_argument("--operator", required=True, help="Identificação do operador responsável")
    parser.add_argument("--reason", required=True, help="Justificativa/referência do incidente")
    parser.add_argument("--apply", action="store_true", help="Confirmar e auditar; padrão: apenas prévia")
    options = parser.parse_args(argv)
    if options.part is not None and options.part < 1:
        parser.error("--part deve ser >= 1")
    if options.note and not options.ticket:
        parser.error("--note exige --ticket")
    try:
        if not Path(options.db).is_file():
            raise ValueError("delivery_database_not_found")
        store = DeliveryStore(options.db, initialize=False)
        result = reconcile(store, thread_id=options.thread, message_id=options.message,
            part=options.part - 1 if options.part is not None else None, ticket_id=options.ticket,
            remote_id=options.remote_id, operator=options.operator, reason=options.reason, apply=options.apply)
    except (ValueError, sqlite3.Error, OSError) as exc:
        error = str(exc) if isinstance(exc, ValueError) else "local_database_error"
        print(json.dumps({"status": "error", "error": error}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
