import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from app.schemas import Metadata, StoredMessage


@dataclass
class SessionLock:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._locks: dict[str, SessionLock] = {}

    @asynccontextmanager
    async def connect(self):
        async with aiosqlite.connect(self.path, timeout=10) as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute("PRAGMA busy_timeout = 10000")
            yield connection

    async def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self.connect() as connection:
            await connection.execute("PRAGMA journal_mode = WAL")
            await connection.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    system_prompt TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    metadata TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_session_order
                ON messages(session_id, id);
            """)
            await connection.commit()

    async def reachable(self) -> bool:
        async with self.connect() as connection:
            await connection.execute("SELECT 1 FROM messages LIMIT 1")
        return True

    @asynccontextmanager
    async def session_lock(self, session_id: str):
        entry = self._locks.setdefault(session_id, SessionLock())
        entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.users -= 1
            if entry.users == 0:
                del self._locks[session_id]

    async def session(self, session_id: str) -> tuple[str | None, list[StoredMessage]]:
        async with self.connect() as connection:
            async with connection.execute(
                "SELECT system_prompt FROM sessions WHERE session_id = ?", (session_id,)
            ) as cursor:
                session = await cursor.fetchone()
            async with connection.execute(
                "SELECT role, content, metadata FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        messages = [
            StoredMessage(
                role=row["role"],
                content=row["content"],
                metadata=json.loads(row["metadata"]) if row["metadata"] else None,
            )
            for row in rows
        ]
        # Histories from the required minimal schema remain readable without a sessions row.
        return session["system_prompt"] if session else None, messages

    async def save_turn(
        self,
        session_id: str,
        system_prompt: str,
        message: str,
        reply: str,
        metadata: Metadata,
    ):
        now = datetime.now(timezone.utc).isoformat()
        async with self.connect() as connection:
            await connection.execute(
                "INSERT INTO sessions (session_id, system_prompt, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET system_prompt = excluded.system_prompt",
                (session_id, system_prompt, now),
            )
            await connection.executemany(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (session_id, "user", message, None, now),
                    (session_id, "assistant", reply, metadata.model_dump_json(), now),
                ],
            )
            await connection.commit()
