import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class BudgetExceeded(Exception):
    pass


class BudgetStore:
    """Persistent atomic counters; no raw client IPs are stored."""

    def __init__(self, path: str, daily_limit: int, rate_limit: int):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path, self.daily_limit, self.rate_limit = path, daily_limit, rate_limit
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS counters (bucket TEXT PRIMARY KEY, count INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO settings VALUES ('rate_salt', ?)", (secrets.token_hex(32),))
            self.salt = db.execute("SELECT value FROM settings WHERE key='rate_salt'").fetchone()[0].encode()

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def reserve(self, bucket: str, limit: int):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT count FROM counters WHERE bucket=?", (bucket,)).fetchone()
            if count and count[0] >= limit:
                raise BudgetExceeded("Request or LLM budget exhausted")
            db.execute("INSERT INTO counters VALUES (?,1) ON CONFLICT(bucket) DO UPDATE SET count=count+1", (bucket,))

    def llm(self):
        self.reserve("llm:" + datetime.now(timezone.utc).strftime("%Y-%m-%d"), self.daily_limit)

    def client(self, address: str):
        digest = hmac.new(self.salt, address.encode(), hashlib.sha256).hexdigest()
        self.reserve("ip:" + datetime.now(timezone.utc).strftime("%Y-%m-%d-%H") + ":" + digest, self.rate_limit)

