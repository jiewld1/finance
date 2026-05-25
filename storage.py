"""
本地 SQLite 存储：自选股管理
"""
import sqlite3
import os
from typing import Optional

DB_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DB_DIR, "stock_tracker.db")


def get_conn():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            market TEXT DEFAULT 'A',
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            note TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()


def add_stock(code: str, name: str, market: str = "A") -> bool:
    conn = get_conn()
    try:
        count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
        if count >= 50:
            return False
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (code, name, market) VALUES (?, ?, ?)",
            (code, name, market)
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def remove_stock(code: str):
    conn = get_conn()
    conn.execute("DELETE FROM watchlist WHERE code=?", (code,))
    conn.commit()
    conn.close()


def get_watchlist() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT code, name, market, note FROM watchlist ORDER BY added_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def is_watched(code: str) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM watchlist WHERE code=?", (code,)).fetchone()
    conn.close()
    return row is not None


def get_count() -> int:
    conn = get_conn()
    cnt = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
    conn.close()
    return cnt


def get_setting(key: str, default: str = "") -> str:
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value)
    )
    conn.commit()
    conn.close()