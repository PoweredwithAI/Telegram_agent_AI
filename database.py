"""
database.py — SQLite user tracking, message logging, booking analytics.
Every interaction is recorded. Owner can query via /stats command.
"""

import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path("bot_data.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id      INTEGER PRIMARY KEY,
                username     TEXT,
                first_name   TEXT,
                last_name    TEXT,
                first_seen   TEXT NOT NULL,
                last_seen    TEXT NOT NULL,
                msg_count    INTEGER DEFAULT 0,
                booking_count INTEGER DEFAULT 0,
                is_blocked   INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                direction   TEXT NOT NULL CHECK(direction IN ('in','out')),
                content     TEXT NOT NULL,
                ts          TEXT NOT NULL,
                is_booking  INTEGER DEFAULT 0,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS bookings (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL,
                calendar_link  TEXT NOT NULL,
                time_preference TEXT,
                status         TEXT DEFAULT 'pending',
                ts             TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            );

            CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id);
            CREATE INDEX IF NOT EXISTS idx_messages_ts   ON messages(ts);
        """)
    logger.info("Database initialised at %s", DB_PATH)


def upsert_user(user_id: int, username: str | None, first_name: str, last_name: str | None) -> None:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO users (user_id, username, first_name, last_name, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username   = excluded.username,
                first_name = excluded.first_name,
                last_name  = excluded.last_name,
                last_seen  = excluded.last_seen
        """, (user_id, username, first_name, last_name or "", now, now))


def increment_msg_count(user_id: int) -> None:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET msg_count = msg_count + 1, last_seen = ? WHERE user_id = ?",
            (now, user_id)
        )


def log_message(user_id: int, direction: str, content: str, is_booking: bool = False) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (user_id, direction, content, ts, is_booking) VALUES (?,?,?,?,?)",
            (user_id, direction, content[:2000], datetime.utcnow().isoformat(), int(is_booking))
        )


def log_booking(user_id: int, calendar_link: str, time_preference: str | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO bookings (user_id, calendar_link, time_preference, ts) VALUES (?,?,?,?)",
            (user_id, calendar_link, time_preference, datetime.utcnow().isoformat())
        )
        conn.execute(
            "UPDATE users SET booking_count = booking_count + 1 WHERE user_id = ?",
            (user_id,)
        )
        return cur.lastrowid


def is_blocked(user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT is_blocked FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return bool(row and row["is_blocked"])


def block_user(user_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE users SET is_blocked = 1 WHERE user_id = ?", (user_id,))


def get_stats_summary() -> dict:
    """Returns a summary dict for the /stats admin command."""
    with get_conn() as conn:
        total_users  = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active_today = conn.execute(
            "SELECT COUNT(*) FROM users WHERE last_seen >= ?",
            ((datetime.utcnow() - timedelta(days=1)).isoformat(),)
        ).fetchone()[0]
        active_week  = conn.execute(
            "SELECT COUNT(*) FROM users WHERE last_seen >= ?",
            ((datetime.utcnow() - timedelta(days=7)).isoformat(),)
        ).fetchone()[0]
        total_msgs   = conn.execute("SELECT COUNT(*) FROM messages WHERE direction='in'").fetchone()[0]
        total_book   = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]

        # Top users by message count
        top_users = conn.execute("""
            SELECT first_name, username, msg_count, booking_count
            FROM users ORDER BY msg_count DESC LIMIT 5
        """).fetchall()

        # Recent bookings
        recent_book = conn.execute("""
            SELECT u.first_name, u.username, b.calendar_link, b.time_preference, b.ts
            FROM bookings b JOIN users u ON b.user_id = u.user_id
            ORDER BY b.ts DESC LIMIT 5
        """).fetchall()

    return {
        "total_users":  total_users,
        "active_today": active_today,
        "active_week":  active_week,
        "total_msgs":   total_msgs,
        "total_book":   total_book,
        "top_users":    [dict(r) for r in top_users],
        "recent_book":  [dict(r) for r in recent_book],
    }