"""
storage.py — Persistent storage
================================
Includes migration: safely adds 'deleted' column to existing databases.
"""

import sqlite3, json, hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional

import config as cfg

PROJECT   = cfg.load()
BRAIN_DIR = Path(PROJECT["brain_dir"])
DB_PATH   = BRAIN_DIR / "brain_history.db"


def _connect() -> sqlite3.Connection:
    BRAIN_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables + run migrations for existing databases."""
    with _connect() as conn:
        # Create tables fresh if they don't exist
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          TEXT PRIMARY KEY,
            title       TEXT,
            mode        TEXT,
            created_at  TEXT,
            updated_at  TEXT,
            turn_count  INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS turns (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            turn_number INTEGER NOT NULL,
            role        TEXT NOT NULL,
            content     TEXT NOT NULL,
            mode        TEXT,
            sources     TEXT,
            chunks_used INTEGER DEFAULT 0,
            created_at  TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );

        CREATE TABLE IF NOT EXISTS insights (
            id          TEXT PRIMARY KEY,
            session_id  TEXT,
            turn_number INTEGER,
            title       TEXT,
            content     TEXT,
            tags        TEXT,
            created_at  TEXT,
            in_chroma   INTEGER DEFAULT 0
        );
        """)

        # ── Migration: add 'deleted' column if it doesn't exist ──────────────
        cols = [r[1] for r in conn.execute("PRAGMA table_info(turns)").fetchall()]
        if "deleted" not in cols:
            conn.execute("ALTER TABLE turns ADD COLUMN deleted INTEGER DEFAULT 0")
            print("  [migration] Added 'deleted' column to turns table")


# ── Sessions ──────────────────────────────────────────────────────────────────

def create_session(mode: str = "brainstorm") -> str:
    sid = hashlib.md5(datetime.now().isoformat().encode()).hexdigest()[:12]
    now = datetime.now().isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, title, mode, created_at, updated_at) VALUES (?,?,?,?,?)",
            (sid, "Untitled session", mode, now, now)
        )
    return sid


def update_session_title(sid: str, first_question: str):
    title = first_question[:60] + ("…" if len(first_question) > 60 else "")
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET title=?, updated_at=? WHERE id=?",
            (title, datetime.now().isoformat(), sid)
        )


def get_all_sessions() -> list[dict]:
    """Only return sessions that have at least one non-deleted turn."""
    with _connect() as conn:
        rows = conn.execute("""
            SELECT s.* FROM sessions s
            WHERE EXISTS (
                SELECT 1 FROM turns t
                WHERE t.session_id = s.id AND t.deleted = 0
            )
            ORDER BY s.updated_at DESC
        """).fetchall()
    return [dict(r) for r in rows]


def get_session(sid: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    return dict(row) if row else None


def delete_session(sid: str):
    """Soft-delete all turns in a session."""
    with _connect() as conn:
        conn.execute("UPDATE turns SET deleted=1 WHERE session_id=?", (sid,))


# ── Turns ─────────────────────────────────────────────────────────────────────

def save_turn(sid: str, turn_number: int, role: str, content: str,
              mode: str = "", sources: list = None, chunks_used: int = 0):
    now = datetime.now().isoformat()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO turns
               (session_id, turn_number, role, content, mode, sources, chunks_used, created_at, deleted)
               VALUES (?,?,?,?,?,?,?,?,0)""",
            (sid, turn_number, role, content, mode,
             json.dumps(sources or []), chunks_used, now)
        )
        conn.execute(
            "UPDATE sessions SET turn_count=?, updated_at=? WHERE id=?",
            (turn_number, now, sid)
        )


def get_session_turns(sid: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM turns WHERE session_id= AND deleted=0 ORDER BY turn_number ASC",
            (sid,)
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["sources"] = json.loads(d.get("sources") or "[]")
        except Exception:
            d["sources"] = []
        result.append(d)
    return result


def soft_delete_turn(turn_id: int):
    """Soft-delete a turn and its pair (user+assistant)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT session_id, turn_number FROM turns WHERE id=?", (turn_id,)
        ).fetchone()
        if not row:
            return
        sid, tnum = row["session_id"], row["turn_number"]
        pair = tnum + 1 if tnum % 2 == 1 else tnum - 1
        conn.execute(
            "UPDATE turns SET deleted=1 WHERE session_id=? AND turn_number IN (?,?)",
            (sid, tnum, pair)
        )
        count = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id= AND deleted=0", (sid,)
        ).fetchone()[0]
        conn.execute("UPDATE sessions SET turn_count=? WHERE id=?", (count, sid))


# ── Earned Insights ───────────────────────────────────────────────────────────

def save_insight(sid: str, turn_number: int, title: str,
                 content: str, tags: list, in_chroma: bool = False) -> str:
    iid = hashlib.md5(f"{sid}_{turn_number}_{title}".encode()).hexdigest()[:16]
    now = datetime.now().isoformat()
    with _connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO insights
               (id, session_id, turn_number, title, content, tags, created_at, in_chroma)
               VALUES (?,?,?,?,?,?,?,?)""",
            (iid, sid, turn_number, title, content, json.dumps(tags), now, int(in_chroma))
        )
    return iid


def get_all_insights() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM insights ORDER BY created_at DESC"
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["tags"] = json.loads(d.get("tags") or "[]")
        except Exception:
            d["tags"] = []
        result.append(d)
    return result


def delete_insight(iid: str):
    with _connect() as conn:
        conn.execute("DELETE FROM insights WHERE id=?", (iid,))


def mark_insight_in_chroma(iid: str):
    with _connect() as conn:
        conn.execute("UPDATE insights SET in_chroma=1 WHERE id=?", (iid,))
