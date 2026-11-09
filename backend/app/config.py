import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple
from urllib.parse import urlsplit

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_path: Path = Path(__file__).resolve().parents[1] / "data/auth.sqlite3"
    calculation_catalog_path: Path = Path(__file__).resolve().parents[1] / "examples/catalog.json"
    network_catalog_path: Path = Path(__file__).resolve().parents[1] / "examples/network.json"
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
    verify_limit: int = 30
    resend_limit: int = 10
    verification_ttl_seconds: int = 600
    verification_cooldown_seconds: int = 60
    verification_max_attempts: int = 5
    verification_email_limit: int = 5
    verification_secret: str = field(default="", repr=False)
    mail_backend: str = "file"
    production: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_security: str = "ssl"
    smtp_username: str = ""
    smtp_password: str = field(default="", repr=False)
    mail_from: str = "no-reply@example.com"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:8b"
    ollama_timeout_seconds: int = 90

    def __post_init__(self):
        for origin in self.allowed_origins:
            parsed = urlsplit(origin)
            if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
                raise ValueError("ALLOWED_ORIGINS должен содержать адреса вида http://localhost:5173 без пути и завершающего /")
        for value in (self.session_ttl_seconds, self.rate_window_seconds, self.login_limit, self.register_limit,
                      self.verify_limit, self.resend_limit, self.verification_ttl_seconds,
                      self.verification_cooldown_seconds, self.verification_max_attempts, self.verification_email_limit):
            if value <= 0:
                raise ValueError("Срок сессии и лимиты должны быть положительными")
        if self.mail_backend not in ("file", "smtp"):
            raise ValueError("MAIL_BACKEND должен быть file или smtp")
        if self.smtp_security not in ("ssl", "starttls"):
            raise ValueError("SMTP_SECURITY должен быть ssl или starttls")
        if self.mail_backend == "smtp" and (not self.smtp_host or not self.smtp_username or not self.smtp_password):
            raise ValueError("Для SMTP нужны SMTP_HOST, SMTP_USERNAME и SMTP_PASSWORD")
        if self.mail_backend == "smtp" and (not 1 <= self.smtp_port <= 65535 or "@" not in self.mail_from or any(c in self.mail_from for c in "\r\n")):
            raise ValueError("Проверьте SMTP_PORT и MAIL_FROM")
        if self.verification_secret and len(self.verification_secret) < 32:
            raise ValueError("VERIFICATION_SECRET должен содержать минимум 32 символа")
        if self.production and (self.mail_backend != "smtp" or not self.verification_secret or not self.cookie_secure):
            raise ValueError("Для production нужны SMTP, VERIFICATION_SECRET и Secure-cookie")
        ollama = urlsplit(self.ollama_url)
        if (ollama.scheme not in ("http", "https") or not ollama.hostname or ollama.path not in ("", "/")
                or ollama.query or ollama.fragment or ollama.username is not None or ollama.password is not None):
            raise ValueError("OLLAMA_URL должен быть базовым HTTP-адресом без пути и учётных данных")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", self.ollama_model):
            raise ValueError("Укажите корректное имя модели в OLLAMA_MODEL")
        if not 1 <= self.ollama_timeout_seconds <= 300:
            raise ValueError("OLLAMA_TIMEOUT_SECONDS должен быть от 1 до 300 секунд")

    @classmethod
    def from_env(cls):
        # Локальные настройки не перекрывают переменные окружения хостинга.
        load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
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
            calculation_catalog_path=Path(os.getenv("CALCULATION_CATALOG_PATH", str(defaults.calculation_catalog_path))),
            network_catalog_path=Path(os.getenv("NETWORK_CATALOG_PATH", str(defaults.network_catalog_path))),
            allowed_origins=origins,
            cookie_secure=secure_raw == "true",
            session_ttl_seconds=int(os.getenv("SESSION_TTL_SECONDS", defaults.session_ttl_seconds)),
            production=production,
            mail_backend=os.getenv("MAIL_BACKEND", "smtp" if production else "file"),
            smtp_host=os.getenv("SMTP_HOST", ""),
            smtp_port=int(os.getenv("SMTP_PORT", "465")),
            smtp_security=os.getenv("SMTP_SECURITY", "ssl"),
            smtp_username=os.getenv("SMTP_USERNAME", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            mail_from=os.getenv("MAIL_FROM", "no-reply@example.com"),
            verification_secret=os.getenv("VERIFICATION_SECRET", ""),
            ollama_url=os.getenv("OLLAMA_URL", defaults.ollama_url),
            ollama_model=os.getenv("OLLAMA_MODEL", defaults.ollama_model),
            ollama_timeout_seconds=int(os.getenv("OLLAMA_TIMEOUT_SECONDS", defaults.ollama_timeout_seconds)),
        )
