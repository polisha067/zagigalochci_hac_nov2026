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


def reset_password(verification, password_hasher, verification_id, code, new_password):
    with verification.database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM password_resets WHERE verification_id = ?", (verification_id,)
        ).fetchone()
        if row is None or row["expires_at"] <= int(time.time()):
            raise VerificationProblem(400, "invalid_reset_code", "Код недействителен или истёк. Запросите новый")
        if row["attempts"] >= verification.settings.verification_max_attempts:
            raise VerificationProblem(429, "reset_attempts_exceeded", "Слишком много неверных кодов. Запросите новый")
        valid = hmac.compare_digest(row["code_hash"], verification.hash_code(verification_id, code))
        if not valid:
            connection.execute(
                "UPDATE password_resets SET attempts = attempts + 1 WHERE verification_id = ?", (verification_id,)
            )
        else:
            user_id = row["user_id"]
            password_hash = password_hasher.hash(new_password)
            confirmed_at = datetime.now(timezone.utc).isoformat()
            connection.execute(
                "UPDATE users SET password_hash = ?, email_verified_at = COALESCE(email_verified_at, ?) WHERE id = ?",
                (password_hash, confirmed_at, user_id),
            )
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            connection.execute("DELETE FROM pending_registrations WHERE existing_user_id = ?", (user_id,))
            connection.execute("DELETE FROM password_resets WHERE verification_id = ?", (verification_id,))
    if not valid:
        raise VerificationProblem(400, "invalid_reset_code", "Код недействителен или истёк. Запросите новый")
