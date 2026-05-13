from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from t9fox.config import ensure_cache_dir


def _db_path() -> Path:
    return ensure_cache_dir() / "t9fox.db"


@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    con = sqlite3.connect(_db_path())
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    """Create tables if they do not exist."""
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT    NOT NULL,
                symbol      TEXT    NOT NULL,
                prev_close  REAL,
                chg_pct     REAL,
                high_20d    REAL,
                gap         REAL,
                gap_pct     REAL,
                status      TEXT,
                created_at  TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(date, symbol)
            );

            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT    NOT NULL,
                symbol      TEXT    NOT NULL,
                action      TEXT    NOT NULL,
                lots        INTEGER NOT NULL,
                price       REAL    NOT NULL,
                order_id    TEXT,
                order_status TEXT,
                strategy    TEXT    DEFAULT 'breakout_20d',
                simulation  INTEGER DEFAULT 1,
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE INDEX IF NOT EXISTS ix_signals_date   ON signals(date);
            CREATE INDEX IF NOT EXISTS ix_signals_symbol ON signals(symbol);
            CREATE INDEX IF NOT EXISTS ix_trades_date    ON trades(date);
            CREATE INDEX IF NOT EXISTS ix_trades_symbol  ON trades(symbol);
        """)


# ── signals ────────────────────────────────────────────────────────────

def upsert_signal(
    date: str, symbol: str,
    prev_close: float, chg_pct: float,
    high_20d: float, gap: float, gap_pct: float,
    status: str,
) -> None:
    init_db()
    with _conn() as con:
        con.execute("""
            INSERT INTO signals
                (date, symbol, prev_close, chg_pct, high_20d, gap, gap_pct, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date, symbol) DO UPDATE SET
                prev_close = excluded.prev_close,
                chg_pct    = excluded.chg_pct,
                high_20d   = excluded.high_20d,
                gap        = excluded.gap,
                gap_pct    = excluded.gap_pct,
                status     = excluded.status,
                created_at = datetime('now','localtime')
        """, (date, symbol, prev_close, chg_pct, high_20d, gap, gap_pct, status))


def query_signals(
    date: str | None = None,
    symbol: str | None = None,
    status: str | None = None,
    limit: int = 200,
) -> list[dict]:
    init_db()
    clauses, params = [], []
    if date:
        clauses.append("date = ?");   params.append(date)
    if symbol:
        clauses.append("symbol = ?"); params.append(symbol)
    if status:
        clauses.append("status = ?"); params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _conn() as con:
        rows = con.execute(
            f"SELECT * FROM signals {where} ORDER BY date DESC, gap ASC LIMIT ?",
            params + [limit],
        ).fetchall()
    return [dict(r) for r in rows]


# ── trades ─────────────────────────────────────────────────────────────

def insert_trade(
    date: str, symbol: str,
    action: str, lots: int, price: float,
    order_id: str, order_status: str,
    simulation: bool = True,
    strategy: str = "breakout_20d",
) -> None:
    init_db()
    with _conn() as con:
        con.execute("""
            INSERT INTO trades
                (date, symbol, action, lots, price, order_id, order_status, simulation, strategy)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (date, symbol, action, lots, price, order_id, order_status,
              1 if simulation else 0, strategy))


def query_trades(
    symbol: str | None = None,
    date: str | None = None,
    limit: int = 200,
) -> list[dict]:
    init_db()
    clauses, params = [], []
    if symbol:
        clauses.append("symbol = ?"); params.append(symbol)
    if date:
        clauses.append("date = ?");   params.append(date)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _conn() as con:
        rows = con.execute(
            f"SELECT * FROM trades {where} ORDER BY created_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
    return [dict(r) for r in rows]
