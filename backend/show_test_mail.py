"""Показать последнее локальное письмо: python -m backend.show_test_mail."""

from email import policy
from email.parser import BytesParser

from backend.app.config import Settings


def main():
    settings = Settings.from_env()
    if settings.mail_backend != "file" or settings.production:
        raise SystemExit("Тестовый ящик доступен только с MAIL_BACKEND=file в development")
    files = list((settings.database_path.parent / "outbox").glob("*.eml"))
    if not files:
        raise SystemExit("Писем пока нет. Сначала выполните регистрацию.")
    latest = max(files, key=lambda path: path.stat().st_mtime_ns)
    message = BytesParser(policy=policy.default).parsebytes(latest.read_bytes())
    print("ТЕСТОВОЕ ПИСЬМО: на настоящий email оно не отправлено.")
    print("Кому:", message["To"])
    print("Тема:", message["Subject"])
    print(message.get_content())


if __name__ == "__main__":
    main()
