"""F4 存档雏形：SQLite 消息库（F1 §6 R6.6：先落库再分发；重启 seq 不回退）。

seq 由 SQLite AUTOINCREMENT 分配：起始 1，全局单序列，不按会话分列（F1 §0）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_id          TEXT NOT NULL UNIQUE,
    conversation_id TEXT NOT NULL,
    from_agent      TEXT NOT NULL,
    mentions        TEXT NOT NULL,          -- JSON array
    type            TEXT NOT NULL,
    body            TEXT NOT NULL,
    ts              TEXT NOT NULL,          -- ISO 8601 UTC
    reply_to        INTEGER
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_conv_seq ON messages(conversation_id, seq);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);
"""


class Store:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    def insert(self, msg: dict) -> int:
        """落库并返回分配的 seq。msg_id 重复时抛 sqlite3.IntegrityError。"""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages(msg_id, conversation_id, from_agent, mentions,"
                " type, body, ts, reply_to) VALUES (?,?,?,?,?,?,?,?)",
                (
                    msg["msg_id"], msg["conversation_id"], msg["from"],
                    json.dumps(msg["mentions"], ensure_ascii=False),
                    msg["type"], msg["body"], msg["ts"], msg["reply_to"],
                ),
            )
            self._conn.commit()
            return cur.lastrowid

    def max_seq(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COALESCE(MAX(seq),0) FROM messages").fetchone()
            return row[0]

    def fetch_after_seq(self, conversation_id: str, after_seq: int, limit: int) -> list:
        """seq 增量拉取（F4）：(after_seq, +∞)，按 seq 升序。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE conversation_id=? AND seq>? "
                "ORDER BY seq ASC LIMIT ?",
                (conversation_id, after_seq, limit),
            ).fetchall()
        return [self._row_to_msg(r) for r in rows]

    def fetch_range_visible(self, conversations: list, after_seq: int, limit: int) -> list:
        """断线补发（R6.4）：多个可见会话、全局 seq 区间，按 seq 升序。"""
        if not conversations:
            return []
        marks = ",".join("?" for _ in conversations)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM messages WHERE conversation_id IN ({marks}) AND seq>? "
                "ORDER BY seq ASC LIMIT ?",
                (*conversations, after_seq, limit),
            ).fetchall()
        return [self._row_to_msg(r) for r in rows]

    def fetch_by_ts(self, conversation_id: str, from_ts: str, to_ts: str, limit: int) -> list:
        """时间段检索（F4），ts 为 ISO 8601 UTC 字符串，字典序即可比较。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE conversation_id=? AND ts>=? AND ts<=? "
                "ORDER BY seq ASC LIMIT ?",
                (conversation_id, from_ts, to_ts, limit),
            ).fetchall()
        return [self._row_to_msg(r) for r in rows]

    def list_conversations(self) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT conversation_id FROM messages ORDER BY conversation_id"
            ).fetchall()
        return [r[0] for r in rows]

    def msg_id_exists(self, msg_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM messages WHERE msg_id=?", (msg_id,)
            ).fetchone()
        return row is not None

    @staticmethod
    def _row_to_msg(row) -> dict:
        return {
            "seq": row[0],
            "msg_id": row[1],
            "conversation_id": row[2],
            "from": row[3],
            "mentions": json.loads(row[4]),
            "type": row[5],
            "body": row[6],
            "ts": row[7],
            "reply_to": row[8],
        }
