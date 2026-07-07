"""SQLite 영속성 계층 — 대화/메시지 저장.

연결은 호출마다 새로 열고 닫아(WAL 모드) 스레드 안전성을 단순하게 확보한다.
async 엔드포인트에서는 asyncio.to_thread 로 감싸 이벤트 루프를 막지 않는다.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "chat.db"


def _now_ms() -> int:
    return int(time.time() * 1000)


def new_id() -> str:
    return uuid.uuid4().hex


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id         TEXT PRIMARY KEY,
                title      TEXT NOT NULL DEFAULT '새 대화',
                model      TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id              TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role            TEXT NOT NULL,
                content         TEXT NOT NULL DEFAULT '',
                images          TEXT NOT NULL DEFAULT '[]',
                created_at      INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_conv
                ON messages(conversation_id, created_at);
            """
        )


# ---------------------------------------------------------------- conversations
def create_conversation(model: str, title: str = "새 대화") -> dict[str, Any]:
    cid = new_id()
    ts = _now_ms()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO conversations (id, title, model, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (cid, title, model, ts, ts),
        )
    return {"id": cid, "title": title, "model": model, "created_at": ts, "updated_at": ts}


def list_conversations() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, title, model, created_at, updated_at "
            "FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_conversation(cid: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, title, model, created_at, updated_at "
            "FROM conversations WHERE id=?",
            (cid,),
        ).fetchone()
        if row is None:
            return None
        conv = dict(row)
        msgs = conn.execute(
            "SELECT id, role, content, images, created_at "
            "FROM messages WHERE conversation_id=? ORDER BY created_at, rowid",
            (cid,),
        ).fetchall()
    conv["messages"] = [
        {
            "id": m["id"],
            "role": m["role"],
            "content": m["content"],
            "images": json.loads(m["images"]),
            "created_at": m["created_at"],
        }
        for m in msgs
    ]
    return conv


def rename_conversation(cid: str, title: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE conversations SET title=?, updated_at=? WHERE id=?",
            (title, _now_ms(), cid),
        )
        return cur.rowcount > 0


def touch_conversation(cid: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE conversations SET updated_at=? WHERE id=?", (_now_ms(), cid)
        )


def delete_conversation(cid: str) -> list[str]:
    """대화를 삭제하고, 정리해야 할 이미지 파일명 목록을 반환한다."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT images FROM messages WHERE conversation_id=?", (cid,)
        ).fetchall()
        files: list[str] = []
        for r in rows:
            files.extend(json.loads(r["images"]))
        conn.execute("DELETE FROM conversations WHERE id=?", (cid,))
    return files


def get_conversation_model(cid: str) -> Optional[str]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT model FROM conversations WHERE id=?", (cid,)
        ).fetchone()
        return row["model"] if row else None


def conversation_exists(cid: str) -> bool:
    with _connect() as conn:
        return (
            conn.execute(
                "SELECT 1 FROM conversations WHERE id=?", (cid,)
            ).fetchone()
            is not None
        )


# --------------------------------------------------------------------- messages
def add_message(
    conversation_id: str,
    role: str,
    content: str,
    images: Optional[list[str]] = None,
) -> dict[str, Any]:
    mid = new_id()
    ts = _now_ms()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, images, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (mid, conversation_id, role, content, json.dumps(images or []), ts),
        )
        conn.execute(
            "UPDATE conversations SET updated_at=? WHERE id=?", (ts, conversation_id)
        )
    return {
        "id": mid,
        "role": role,
        "content": content,
        "images": images or [],
        "created_at": ts,
    }


def update_message_content(mid: str, content: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE messages SET content=? WHERE id=?", (content, mid))


def get_messages(conversation_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, role, content, images, created_at "
            "FROM messages WHERE conversation_id=? ORDER BY created_at, rowid",
            (conversation_id,),
        ).fetchall()
    return [
        {
            "id": m["id"],
            "role": m["role"],
            "content": m["content"],
            "images": json.loads(m["images"]),
            "created_at": m["created_at"],
        }
        for m in rows
    ]


def message_count(conversation_id: str) -> int:
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()[0]


def delete_last_assistant_message(conversation_id: str) -> list[str]:
    """마지막 메시지가 assistant면 삭제하고 그 이미지 파일명을 반환(재생성용)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, role, images FROM messages WHERE conversation_id=? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        if row is None or row["role"] != "assistant":
            return []
        conn.execute("DELETE FROM messages WHERE id=?", (row["id"],))
        return json.loads(row["images"])
