import hmac
import logging
import time
from datetime import datetime, timezone

from .mailer import MailDeliveryError
from .verification import VerificationProblem


logger = logging.getLogger(__name__)


def send_reset_code(verification, email, verification_id, expires_at):
    """Вызывается после одинакового ответа API для любого адреса."""
    try:
        with verification.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = int(time.time())
            connection.execute("DELETE FROM password_resets WHERE expires_at <= ?", (now,))
            user = connection.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
            if user is None or expires_at <= now:
                return
            code = verification.new_code(verification_id)
            connection.execute("DELETE FROM password_resets WHERE user_id = ?", (user["id"],))
            connection.execute(
                "INSERT INTO password_resets (verification_id, user_id, code_hash, expires_at) VALUES (?, ?, ?, ?)",
                (verification_id, user["id"], verification.hash_code(verification_id, code), expires_at),
            )
            verification.mailer.send_password_reset(email, code)
    except MailDeliveryError:
        logger.warning("Не удалось отправить код восстановления пароля")


def check_reset_code(verification, verification_id, code):
    with verification.database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        user_id = checked_reset_user(connection, verification, verification_id, code)
    if user_id is None:
        raise invalid_reset_code()


def invalid_reset_code():
    return VerificationProblem(400, "invalid_reset_code", "Код недействителен или истёк. Проверьте код или запросите новый")


def checked_reset_user(connection, verification, verification_id, code):
    row = connection.execute(
        "SELECT * FROM password_resets WHERE verification_id = ?", (verification_id,)
    ).fetchone()
    if row is None or row["expires_at"] <= int(time.time()):
        raise invalid_reset_code()
    if row["attempts"] >= verification.settings.verification_max_attempts:
        raise VerificationProblem(429, "reset_attempts_exceeded", "Слишком много неверных кодов. Запросите новый")
    if hmac.compare_digest(row["code_hash"], verification.hash_code(verification_id, code)):
        return row["user_id"]
    connection.execute(
        "UPDATE password_resets SET attempts = attempts + 1 WHERE verification_id = ?", (verification_id,)
    )
    return None


def reset_password(verification, password_hasher, verification_id, code, new_password, *, start_session):
    with verification.database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        user_id = checked_reset_user(connection, verification, verification_id, code)
        if user_id is not None:
            password_hash = password_hasher.hash(new_password)
            confirmed_at = datetime.now(timezone.utc).isoformat()
            connection.execute(
                "UPDATE users SET password_hash = ?, email_verified_at = COALESCE(email_verified_at, ?) WHERE id = ?",
                (password_hash, confirmed_at, user_id),
            )
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            connection.execute("DELETE FROM pending_registrations WHERE existing_user_id = ?", (user_id,))
            connection.execute("DELETE FROM password_resets WHERE verification_id = ?", (verification_id,))
            user = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            token = start_session(connection, user_id)
    if user_id is None:
        raise invalid_reset_code()
    return user, token
