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

    CREATE TABLE IF NOT EXISTS virtual_cards (
        id TEXT PRIMARY KEY,
        provider TEXT,
        alias TEXT,
        merchant_lock TEXT,
        purpose TEXT,
        currency TEXT,
        spend_limit REAL,
        available_limit REAL,
        status TEXT,
        network TEXT,
        masked_pan TEXT,
        session_id TEXT,
        integration_mode TEXT,
        created_at TEXT,
        last_used_at TEXT,
        raw_json TEXT
    );

    CREATE TABLE IF NOT EXISTS virtual_card_transactions (
        id TEXT PRIMARY KEY,
        provider_transaction_id TEXT,
        card_id TEXT NOT NULL,
        amount REAL NOT NULL,
        reason TEXT,
        status TEXT,
        integration_mode TEXT,
        session_id TEXT,
        timestamp TEXT,
        remaining_limit REAL,
        raw_json TEXT
    );

    CREATE TABLE IF NOT EXISTS gst_runs (
        run_id TEXT PRIMARY KEY,
        status TEXT,
        portal TEXT,
        gstin TEXT,
        filing_period TEXT,
        tax_amount REAL,
        notes TEXT,
        session_id TEXT,
        card_id TEXT,
        card_transaction_json TEXT,
        integration_mode TEXT,
        receipt_ref TEXT,
        receipt_url TEXT,
        steps_json TEXT,
        completed_at TEXT,
        raw_json TEXT
    );
    """)

    conn.commit()
    conn.close()
    print("[DB] SQLite initialized at", DB_PATH)


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row) if row is not None else {}


def _safe_json_loads(raw: Any, fallback: Any) -> Any:
    if raw is None:
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


def is_db_ready() -> bool:
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        conn.close()
        return True
    except Exception:
        return False


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


def upsert_virtual_card(record: Dict[str, Any]) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO virtual_cards (
            id, provider, alias, merchant_lock, purpose, currency, spend_limit, available_limit,
            status, network, masked_pan, session_id, integration_mode, created_at, last_used_at, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.get("id"),
            record.get("provider"),
            record.get("alias"),
            record.get("merchant_lock"),
            record.get("purpose"),
            record.get("currency"),
            record.get("spend_limit"),
            record.get("available_limit"),
            record.get("status"),
            record.get("network"),
            record.get("masked_pan"),
            record.get("session_id"),
            record.get("integration_mode"),
            record.get("created_at"),
            record.get("last_used_at"),
            json.dumps(record, ensure_ascii=True),
        ),
    )
    conn.commit()
    conn.close()


def get_virtual_card_record(card_id: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT raw_json FROM virtual_cards WHERE id = ?", (card_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    item = _row_to_dict(row)
    return _safe_json_loads(item.get("raw_json"), {})


def list_virtual_card_records(status: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    if status:
        cur.execute(
            """
            SELECT raw_json FROM virtual_cards
            WHERE lower(status) = lower(?)
            ORDER BY created_at DESC
            """,
            (status,),
        )
    else:
        cur.execute(
            """
            SELECT raw_json FROM virtual_cards
            ORDER BY created_at DESC
            """
        )

    rows = [_safe_json_loads(_row_to_dict(row).get("raw_json"), {}) for row in cur.fetchall()]
    conn.close()
    return [row for row in rows if isinstance(row, dict)]


def insert_virtual_card_transaction(record: Dict[str, Any]) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO virtual_card_transactions (
            id, provider_transaction_id, card_id, amount, reason, status, integration_mode,
            session_id, timestamp, remaining_limit, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.get("id"),
            record.get("provider_transaction_id"),
            record.get("card_id"),
            record.get("amount", 0.0),
            record.get("reason"),
            record.get("status"),
            record.get("integration_mode"),
            record.get("session_id"),
            record.get("timestamp"),
            record.get("remaining_limit"),
            json.dumps(record, ensure_ascii=True),
        ),
    )
    conn.commit()
    conn.close()


def list_virtual_card_transaction_records(card_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    safe_limit = max(0, limit)
    if card_id:
        cur.execute(
            """
            SELECT raw_json FROM virtual_card_transactions
            WHERE card_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (card_id, safe_limit),
        )
    else:
        cur.execute(
            """
            SELECT raw_json FROM virtual_card_transactions
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (safe_limit,),
        )
    rows = [_safe_json_loads(_row_to_dict(row).get("raw_json"), {}) for row in cur.fetchall()]
    conn.close()
    cleaned = [row for row in rows if isinstance(row, dict)]
    return list(reversed(cleaned))


def upsert_gst_run(record: Dict[str, Any]) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO gst_runs (
            run_id, status, portal, gstin, filing_period, tax_amount, notes, session_id, card_id,
            card_transaction_json, integration_mode, receipt_ref, receipt_url, steps_json, completed_at, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.get("run_id"),
            record.get("status"),
            record.get("portal"),
            record.get("gstin"),
            record.get("filing_period"),
            record.get("tax_amount", 0.0),
            record.get("notes"),
            record.get("session_id"),
            record.get("card_id"),
            json.dumps(record.get("card_transaction"), ensure_ascii=True),
            record.get("integration_mode"),
            record.get("receipt_ref"),
            record.get("receipt_url"),
            json.dumps(record.get("steps", []), ensure_ascii=True),
            record.get("completed_at"),
            json.dumps(record, ensure_ascii=True),
        ),
    )
    conn.commit()
    conn.close()


def list_gst_run_records(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT raw_json FROM gst_runs
        ORDER BY completed_at DESC
        LIMIT ?
        """,
        (max(0, limit),),
    )
    rows = [_safe_json_loads(_row_to_dict(row).get("raw_json"), {}) for row in cur.fetchall()]
    conn.close()
    cleaned = [row for row in rows if isinstance(row, dict)]
    return list(reversed(cleaned))
