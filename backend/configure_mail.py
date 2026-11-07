"""Локальная настройка отправителя: python -m backend.configure_mail."""

import argparse
from getpass import getpass
import os
from pathlib import Path

from dotenv import dotenv_values, set_key
from email_validator import EmailNotValidError, validate_email


def read_password():
    password = getpass("Пароль приложения почты (не обычный пароль аккаунта): ")
    if not password:
        raise SystemExit("Пароль не указан. Настройки не сохранены")
    if not password.isascii() or any(c in password for c in "\r\n"):
        raise SystemExit("Пароль не сохранён: скопируйте только пароль приложения, без русского текста и переносов строк.")
    return password


def main():
    parser = argparse.ArgumentParser(description="Локальная настройка SMTP")
    parser.add_argument("--password-only", action="store_true", help="Заменить только сохранённый пароль")
    args = parser.parse_args()
    path = Path(__file__).resolve().parent / ".env"
    if args.password_only:
        values = dotenv_values(path) if path.exists() else {}
        if not values.get("SMTP_USERNAME") or not values.get("SMTP_HOST"):
            raise SystemExit("Сначала запустите настройку без --password-only")
        password = read_password()
        set_key(str(path), "SMTP_PASSWORD", password)
        path.chmod(0o600)
        print("Пароль обновлён. Остальные настройки сохранены. Перезапустите сервер.")
        return
    print("Настройка SMTP. Пароль вводится скрыто и сохраняется только в backend/.env.")
    address = input("Адрес созданного ящика сервиса: ").strip()
    try:
        address = validate_email(address, check_deliverability=False).normalized
    except EmailNotValidError:
        raise SystemExit("Некорректный адрес почты")
    host = input("SMTP-сервер [smtp.yandex.ru]: ").strip() or "smtp.yandex.ru"
    security = input("Защита ssl или starttls [ssl]: ").strip().lower() or "ssl"
    if security not in ("ssl", "starttls"):
        raise SystemExit("Выберите ssl или starttls")
    default_port = "465" if security == "ssl" else "587"
    port = input(f"Порт [{default_port}]: ").strip() or default_port
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise SystemExit("Некорректный порт")
    username = input(f"Логин SMTP [{address}]: ").strip() or address
    password = read_password()
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT, 0o600)
    os.close(descriptor)
    values = {
        "MAIL_BACKEND": "smtp", "MAIL_FROM": address, "SMTP_HOST": host,
        "SMTP_PORT": port, "SMTP_SECURITY": security,
        "SMTP_USERNAME": username, "SMTP_PASSWORD": password,
    }
    for key, value in values.items():
        set_key(str(path), key, value)
    path.chmod(0o600)
    print("Настройки сохранены в backend/.env. Перезапустите сервер.")
    print("Письма ещё не отправлялись. Проверьте регистрацию на своей почте после запуска.")


if __name__ == "__main__":
    main()
