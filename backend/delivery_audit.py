"""Read-only outbox audit. Never sends messages, retries, or updates receipts."""
import argparse
import json
from pathlib import Path
import sqlite3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="Persistent delivery SQLite path")
    parser.add_argument("--reconciliations", action="store_true", help="Show reconciliation audit trail")
    parser.add_argument("--inputs", action="store_true", help="Show durable pending input IDs")
    options = parser.parse_args()
    uri = Path(options.db).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if options.reconciliations or options.inputs:
            query = ("SELECT * FROM delivery_reconciliations ORDER BY id" if options.reconciliations else
                     "SELECT thread_id,message_id,created_at FROM inbound_pending ORDER BY created_at")
            for row in conn.execute(query):
                print(json.dumps(dict(row), ensure_ascii=False))
            return
        rows = conn.execute("""SELECT d.thread_id,d.message_id,d.sent_parts,d.complete,d.payload,
            d.handoff_note_state,d.handoff_note_id,
            a.part,a.state,a.attempted_at,a.remote_id FROM deliveries d
            LEFT JOIN delivery_attempts a ON a.thread_id=d.thread_id AND a.message_id=d.message_id
            WHERE d.complete=0 OR json_extract(d.payload,'$.blocked_reason') IS NOT NULL
            ORDER BY d.rowid,a.part""").fetchall()
        for row in rows:
            record = dict(row)
            record["blocked_reason"] = json.loads(record.pop("payload")).get("blocked_reason")
            print(json.dumps(record, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
