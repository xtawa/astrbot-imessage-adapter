import sqlite3
import time
from pathlib import Path


class SeenStore:
    """Persist only delivery IDs, never message content or credentials."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, at REAL NOT NULL)"
        )
        self.prune()

    def contains(self, key: str) -> bool:
        return (
            self.db.execute("SELECT 1 FROM seen WHERE key = ?", (key,)).fetchone()
            is not None
        )

    def add(self, key: str):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO seen VALUES (?, ?)", (key, time.time())
            )

    def prune(self):
        with self.db:
            self.db.execute("DELETE FROM seen WHERE at < ?", (time.time() - 7 * 86400,))
            self.db.execute(
                "DELETE FROM seen WHERE key IN (SELECT key FROM seen ORDER BY at DESC LIMIT -1 OFFSET 50000)"
            )

    def close(self):
        self.db.close()
