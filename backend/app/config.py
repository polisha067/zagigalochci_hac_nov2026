import os
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    database_path: Path = Path(__file__).resolve().parents[1] / "data/auth.sqlite3"
    allowed_origins: Tuple[str, ...] = (
        "http://localhost:5173", "http://localhost:3000", "http://localhost:8000",
        "http://127.0.0.1:5173", "http://127.0.0.1:3000", "http://127.0.0.1:8000",
    )
    cookie_name: str = "a1_session"
    cookie_secure: bool = False
    session_ttl_seconds: int = 7 * 24 * 60 * 60
    rate_window_seconds: int = 15 * 60
    login_limit: int = 10
    register_limit: int = 5

    def __post_init__(self):
        for origin in self.allowed_origins:
            parsed = urlsplit(origin)
            if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
                raise ValueError("ALLOWED_ORIGINS должен содержать адреса вида http://localhost:5173 без пути и завершающего /")
        for value in (self.session_ttl_seconds, self.rate_window_seconds, self.login_limit, self.register_limit):
            if value <= 0:
                raise ValueError("Срок сессии и лимиты должны быть положительными")

    @classmethod
    def from_env(cls):
        production = os.getenv("APP_ENV", "development") == "production"
        secure_raw = os.getenv("COOKIE_SECURE", "true" if production else "false").lower()
        if secure_raw not in ("true", "false"):
            raise ValueError("COOKIE_SECURE должен быть true или false")
        if production and secure_raw != "true":
            raise ValueError("Для production необходим COOKIE_SECURE=true и HTTPS")
        defaults = cls()
        origins = tuple(part.strip() for part in os.getenv("ALLOWED_ORIGINS", ",".join(defaults.allowed_origins)).split(",") if part.strip())
        if not origins:
            raise ValueError("Укажите хотя бы один ALLOWED_ORIGINS")
        return cls(
            database_path=Path(os.getenv("DATABASE_PATH", str(defaults.database_path))),
            allowed_origins=origins,
            cookie_secure=secure_raw == "true",
            session_ttl_seconds=int(os.getenv("SESSION_TTL_SECONDS", defaults.session_ttl_seconds)),
        )
