import hashlib
import hmac
import os
import secrets
import tempfile
import time


def load_secret(settings):
    if settings.verification_secret:
        return settings.verification_secret.encode("utf-8")
    # Локальный секрет переживает перезапуск, но не хранится в базе или Git.
    path = settings.database_path.parent / "verification.key"
    if not path.exists():
        descriptor, temporary = tempfile.mkstemp(dir=str(path.parent))
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(secrets.token_bytes(32))
            try:
                os.link(temporary, str(path))
            except FileExistsError:
                pass
        finally:
            os.unlink(temporary)
    secret = path.read_bytes()
    if len(secret) < 32:
        raise ValueError("Локальный секрет подтверждения повреждён")
    return secret


class VerificationProblem(Exception):
    def __init__(self, status, code, message, retry=None):
        self.status, self.code, self.message, self.retry = status, code, message, retry


class VerificationService:
    def __init__(self, database, settings, mailer):
        self.database, self.settings, self.mailer = database, settings, mailer
        self.secret = load_secret(settings)

    def hash_code(self, verification_id, code):
        return hmac.new(self.secret, (verification_id + ":" + code).encode("ascii"), hashlib.sha256).hexdigest()

    def new_code(self, verification_id, previous_hash=None):
        while True:
            code = f"{secrets.randbelow(1000000):06d}"
            if self.hash_code(verification_id, code) != previous_hash:
                return code

    def info(self, row):
        now = int(time.time())
        return {
            "status": "verification_required",
            "verification_id": row["verification_id"],
            "email": row["email"],
            "expires_in": max(0, row["expires_at"] - now),
            "resend_after": max(0, row["sent_at"] + self.settings.verification_cooldown_seconds - now),
        }

    def limit_email(self, email):
        key = "verification-email:" + hashlib.sha256(email.encode("utf-8")).hexdigest()
        retry = self.database.consume_attempt(key, int(time.time()), 3600, self.settings.verification_email_limit)
        if retry:
            raise VerificationProblem(429, "too_many_attempts", "Достигнут лимит писем для этой почты", retry)

    def check_cooldown(self, row, now):
        if row is not None:
            retry = row["sent_at"] + self.settings.verification_cooldown_seconds - now
            if retry > 0:
                raise VerificationProblem(429, "resend_too_soon", "Подождите перед повторной отправкой", retry)

    def issue(self, email, name, password_hash, existing_user_id=None):
        now = int(time.time())
        with self.database.connect() as connection:
            previous = connection.execute("SELECT * FROM pending_registrations WHERE email = ?", (email,)).fetchone()
        self.check_cooldown(previous, now)
        self.limit_email(email)
        verification_id = secrets.token_urlsafe(32)
        code = self.new_code(verification_id)
        row = {
            "verification_id": verification_id, "email": email, "name": name,
            "password_hash": password_hash, "existing_user_id": existing_user_id,
            "code_hash": self.hash_code(verification_id, code),
            "expires_at": now + self.settings.verification_ttl_seconds, "sent_at": now,
        }
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            user = connection.execute("SELECT id, email_verified_at FROM users WHERE email = ?", (email,)).fetchone()
            if user and (existing_user_id != user["id"] or user["email_verified_at"] is not None):
                raise VerificationProblem(409, "email_already_registered", "Аккаунт с такой почтой уже существует")
            previous = connection.execute("SELECT * FROM pending_registrations WHERE email = ?", (email,)).fetchone()
            self.check_cooldown(previous, now)
            connection.execute("DELETE FROM pending_registrations WHERE email = ?", (email,))
            connection.execute(
                "INSERT INTO pending_registrations (verification_id, email, name, password_hash, existing_user_id, code_hash, expires_at, sent_at) VALUES (:verification_id, :email, :name, :password_hash, :existing_user_id, :code_hash, :expires_at, :sent_at)", row,
            )
            # Ошибка отправки откатывает изменение кода; старый код не теряется.
            self.mailer.send_verification(email, code)
        return self.info(row)

    def resend(self, verification_id):
        with self.database.connect() as connection:
            pending = connection.execute("SELECT * FROM pending_registrations WHERE verification_id = ?", (verification_id,)).fetchone()
        if pending is None:
            raise VerificationProblem(400, "invalid_verification", "Запрос подтверждения не найден. Начните регистрацию заново")
        now = int(time.time())
        self.check_cooldown(pending, now)
        self.limit_email(pending["email"])
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM pending_registrations WHERE verification_id = ?", (verification_id,)).fetchone()
            if row is None:
                raise VerificationProblem(400, "invalid_verification", "Запрос подтверждения уже использован")
            self.check_cooldown(row, now)
            code = self.new_code(verification_id, row["code_hash"])
            expires = now + self.settings.verification_ttl_seconds
            connection.execute("UPDATE pending_registrations SET code_hash = ?, expires_at = ?, sent_at = ?, attempts = 0 WHERE verification_id = ?", (self.hash_code(verification_id, code), expires, now, verification_id))
            self.mailer.send_verification(row["email"], code)
            updated = dict(row, expires_at=expires, sent_at=now)
        return self.info(updated)

    def check(self, connection, verification_id, code):
        """Возвращает (строка, ошибка). Ошибку поднимают ПОСЛЕ коммита счётчика попыток."""
        row = connection.execute("SELECT * FROM pending_registrations WHERE verification_id = ?", (verification_id,)).fetchone()
        if row is None:
            return None, VerificationProblem(400, "invalid_verification", "Запрос подтверждения не найден или уже использован")
        if row["expires_at"] <= int(time.time()):
            return None, VerificationProblem(400, "code_expired", "Код истёк. Запросите новый")
        if row["attempts"] >= self.settings.verification_max_attempts:
            return None, VerificationProblem(429, "verification_attempts_exceeded", "Слишком много неверных кодов. Запросите новый")
        if not hmac.compare_digest(row["code_hash"], self.hash_code(verification_id, code)):
            connection.execute("UPDATE pending_registrations SET attempts = attempts + 1 WHERE verification_id = ?", (verification_id,))
            return None, VerificationProblem(400, "invalid_code", "Неверный код подтверждения")
        return row, None
