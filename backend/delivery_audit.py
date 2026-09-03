"""Read-only outbox audit. Never sends messages, retries, or updates receipts."""
import argparse
import json
from pathlib import Path
import sqlite3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="Persistent delivery SQLite path")
    options = parser.parse_args()
    uri = Path(options.db).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""SELECT d.thread_id,d.message_id,d.sent_parts,d.complete,d.payload,
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
