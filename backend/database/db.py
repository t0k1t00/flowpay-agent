"""SQLite Database Initialization"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "paygentic.db")


def get_conn():
    return sqlite3.connect(DB_PATH)


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
    """)

    conn.commit()
    conn.close()
    print("[DB] SQLite initialized at", DB_PATH)
