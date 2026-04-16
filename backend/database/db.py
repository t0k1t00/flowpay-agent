"""SQLite database initialization and persistence helpers."""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "paygentic.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS suppliers (
        id TEXT PRIMARY KEY,
        company_name TEXT NOT NULL,
        price_per_kg REAL,
        delivery_days INTEGER,
        verified INTEGER DEFAULT 0,
        gstin TEXT,
        email TEXT,
        phone TEXT,
        location TEXT,
        website TEXT,
        score INTEGER DEFAULT 0,
        category TEXT DEFAULT 'General',
        recommended INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS escrows (
        id TEXT PRIMARY KEY,
        supplier TEXT NOT NULL,
        amount REAL NOT NULL,
        status TEXT DEFAULT 'pending',
        requires_approval INTEGER DEFAULT 0,
        session_id TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        released_at TEXT,
        refunded_at TEXT
    );

    CREATE TABLE IF NOT EXISTS audit_log (
        id TEXT PRIMARY KEY,
        action TEXT NOT NULL,
        entity TEXT,
        amount REAL DEFAULT 0,
        status TEXT,
        session_id TEXT,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS wallet_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        balance REAL NOT NULL,
        spent REAL NOT NULL,
        escrow_locked REAL NOT NULL,
        spent_today REAL NOT NULL,
        day_key TEXT NOT NULL,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS wallet_ledger (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        amount REAL NOT NULL,
        session_id TEXT,
        status TEXT,
        metadata_json TEXT,
        timestamp TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS email_log (
        id TEXT PRIMARY KEY,
        supplier_email TEXT,
        subject TEXT,
        status TEXT DEFAULT 'sent',
        session_id TEXT,
        sent_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        query TEXT,
        parsed_material TEXT,
        parsed_quantity REAL,
        parsed_budget REAL,
        parsed_delivery INTEGER,
        status TEXT DEFAULT 'running',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS agent_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.commit()
    conn.close()
    print("[DB] SQLite initialized at", DB_PATH)


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row) if row is not None else {}


def insert_audit_log(entry: Dict[str, Any]) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO audit_log (id, action, entity, amount, status, session_id, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry.get("id"),
            entry.get("action"),
            entry.get("entity"),
            entry.get("amount", 0),
            entry.get("status"),
            entry.get("session_id"),
            entry.get("timestamp"),
        ),
    )
    conn.commit()
    conn.close()


def list_audit_logs(session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    if session_id:
        cur.execute(
            """
            SELECT id, action, entity, amount, status, session_id, timestamp
            FROM audit_log WHERE session_id = ? ORDER BY timestamp ASC
            """,
            (session_id,),
        )
    else:
        cur.execute(
            """
            SELECT id, action, entity, amount, status, session_id, timestamp
            FROM audit_log ORDER BY timestamp ASC
            """
        )
    rows = [_row_to_dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def upsert_wallet_state(state: Dict[str, Any]) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO wallet_state (id, balance, spent, escrow_locked, spent_today, day_key, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id)
        DO UPDATE SET
            balance = excluded.balance,
            spent = excluded.spent,
            escrow_locked = excluded.escrow_locked,
            spent_today = excluded.spent_today,
            day_key = excluded.day_key,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            state.get("balance", 0.0),
            state.get("spent", 0.0),
            state.get("escrow_locked", 0.0),
            state.get("spent_today", 0.0),
            state.get("day_key", ""),
        ),
    )
    conn.commit()
    conn.close()


def get_wallet_state_record() -> Optional[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT balance, spent, escrow_locked, spent_today, day_key
        FROM wallet_state WHERE id = 1
        """
    )
    row = cur.fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def insert_wallet_ledger_entry(entry: Dict[str, Any]) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO wallet_ledger (id, kind, amount, session_id, status, metadata_json, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry.get("id"),
            entry.get("kind"),
            entry.get("amount", 0.0),
            entry.get("session_id"),
            entry.get("status"),
            json.dumps(entry.get("metadata", {}), ensure_ascii=True),
            entry.get("timestamp"),
        ),
    )
    conn.commit()
    conn.close()


def list_wallet_ledger_entries(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, kind, amount, session_id, status, metadata_json, timestamp
        FROM wallet_ledger
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (max(0, limit),),
    )
    rows = []
    for row in cur.fetchall():
        item = _row_to_dict(row)
        item["metadata"] = json.loads(item.get("metadata_json") or "{}")
        item.pop("metadata_json", None)
        rows.append(item)
    conn.close()
    return list(reversed(rows))


def upsert_escrow(record: Dict[str, Any]) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO escrows (
            id, supplier, amount, status, requires_approval, session_id, created_at, released_at, refunded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.get("id"),
            record.get("supplier"),
            record.get("amount", 0.0),
            record.get("status"),
            1 if record.get("requires_approval") else 0,
            record.get("session_id"),
            record.get("created_at"),
            record.get("released_at"),
            record.get("refunded_at"),
        ),
    )
    conn.commit()
    conn.close()


def list_escrow_records(session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    if session_id:
        cur.execute(
            """
            SELECT id, supplier, amount, status, requires_approval, session_id, created_at, released_at, refunded_at
            FROM escrows
            WHERE session_id = ?
            ORDER BY created_at DESC
            """,
            (session_id,),
        )
    else:
        cur.execute(
            """
            SELECT id, supplier, amount, status, requires_approval, session_id, created_at, released_at, refunded_at
            FROM escrows
            ORDER BY created_at DESC
            """
        )
    rows = []
    for row in cur.fetchall():
        item = _row_to_dict(row)
        item["requires_approval"] = bool(item.get("requires_approval"))
        rows.append(item)
    conn.close()
    return rows


def insert_agent_event(session_id: str, event_type: str, payload: Dict[str, Any]) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO agent_events (session_id, event_type, payload_json)
        VALUES (?, ?, ?)
        """,
        (session_id, event_type, json.dumps(payload, ensure_ascii=True)),
    )
    conn.commit()
    conn.close()


def upsert_session(session: Dict[str, Any]) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO sessions (id, query, status, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id)
        DO UPDATE SET
            query = excluded.query,
            status = excluded.status,
            created_at = COALESCE(sessions.created_at, excluded.created_at)
        """,
        (
            session.get("session_id") or session.get("id"),
            session.get("query"),
            session.get("status", "queued"),
            session.get("created_at"),
        ),
    )
    conn.commit()
    conn.close()


def list_sessions() -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, query, status, created_at
        FROM sessions
        ORDER BY created_at DESC
        """
    )
    rows = [_row_to_dict(row) for row in cur.fetchall()]
    conn.close()
    return rows
