from contextlib import contextmanager
import sqlite3


class Database:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(str(self.path), timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
                CREATE TABLE IF NOT EXISTS calculation_history (
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    snapshot_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (user_id, snapshot_id)
                );
                CREATE TABLE IF NOT EXISTS auth_limits (
                    bucket TEXT PRIMARY KEY,
                    count INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS password_resets (
                    verification_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                    code_hash TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS pending_registrations (
                    verification_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    existing_user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
                    code_hash TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    sent_at INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0
                );
            """)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
            if "email_verified_at" not in columns:
                connection.execute("ALTER TABLE users ADD COLUMN email_verified_at TEXT")
            # Старые аккаунты не считаем подтверждёнными автоматически.
            connection.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE email_verified_at IS NULL)")

    def consume_attempt(self, bucket, now, window, limit):
        # Одна транзакция не позволяет параллельным запросам обходить лимит.
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM auth_limits WHERE expires_at <= ?", (now,))
            row = connection.execute("SELECT count, expires_at FROM auth_limits WHERE bucket = ?", (bucket,)).fetchone()
            if row is not None and row["count"] >= limit:
                return max(1, row["expires_at"] - now)
            if row is None:
                connection.execute("INSERT INTO auth_limits VALUES (?, 1, ?)", (bucket, now + window))
            else:
                connection.execute("UPDATE auth_limits SET count = count + 1 WHERE bucket = ?", (bucket,))
        return None
