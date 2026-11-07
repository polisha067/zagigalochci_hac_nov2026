from dataclasses import replace
import hashlib
import sqlite3
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.mailer import MailDeliveryError, Mailer
from backend.app.main import create_app
from test_auth import AuthTests as _AuthTests


# Наследуем только вспомогательные методы, а не весь набор тестов авторизации.
class VerificationTests(unittest.TestCase):
    setUp = _AuthTests.setUp
    last_code = _AuthTests.last_code

    def begin(self):
        response = self.client.post("/auth/register", json=self.account)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertNotIn("set-cookie", response.headers)
        return response.json()["verification_id"], self.last_code()

    def verify(self, verification_id, code):
        return self.client.post("/auth/verify-email", json={"verification_id": verification_id, "code": code})

    def enable_resend(self):
        with self.app.state.database.connect() as connection:
            connection.execute("UPDATE pending_registrations SET sent_at = ?", (int(time.time()) - 61,))

    def test_pending_cannot_login_or_access_me(self):
        verification_id, code = self.begin()
        self.assertEqual(self.client.get("/auth/me").status_code, 401)
        response = self.client.post("/auth/login", json={"email": self.account["email"], "password": self.account["password"]})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["verification_id"], verification_id)
        self.assertNotIn("set-cookie", response.headers)
        self.assertNotIn(code, response.text)

    def test_account_created_only_after_confirmation(self):
        verification_id, code = self.begin()
        with self.app.state.database.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM users").fetchone()[0], 0)
            row = connection.execute("SELECT * FROM pending_registrations").fetchone()
            self.assertNotEqual(row["code_hash"], hashlib.sha256(code.encode()).hexdigest())
            self.assertNotEqual(code, row["code_hash"])
        self.assertEqual(self.verify(verification_id, code).status_code, 200)
        with self.app.state.database.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM pending_registrations").fetchone()[0], 0)
            self.assertIsNotNone(connection.execute("SELECT email_verified_at FROM users").fetchone()[0])

    def test_used_code_cannot_create_second_session(self):
        verification_id, code = self.begin()
        self.assertEqual(self.verify(verification_id, code).status_code, 200)
        self.client.post("/auth/logout")
        self.assertEqual(self.verify(verification_id, code).status_code, 400)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_wrong_attempts_persist_and_block_even_correct_code(self):
        verification_id, code = self.begin()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            self.assertEqual(self.verify(verification_id, wrong).status_code, 400)
        with self.app.state.database.connect() as connection:
            self.assertEqual(connection.execute("SELECT attempts FROM pending_registrations").fetchone()[0], 5)
        response = self.verify(verification_id, code)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "verification_attempts_exceeded")

    def test_expired_code_rejected(self):
        verification_id, code = self.begin()
        with self.app.state.database.connect() as connection:
            connection.execute("UPDATE pending_registrations SET expires_at = ?", (int(time.time()) - 1,))
        response = self.verify(verification_id, code)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "code_expired")

    def test_resend_cooldown_and_invalidation(self):
        verification_id, code = self.begin()
        early = self.client.post("/auth/resend-verification", json={"verification_id": verification_id})
        self.assertEqual(early.status_code, 429)
        self.assertGreater(int(early.headers["retry-after"]), 0)
        self.enable_resend()
        response = self.client.post("/auth/resend-verification", json={"verification_id": verification_id})
        self.assertEqual(response.status_code, 202, response.text)
        new_code = self.last_code()
        self.assertNotEqual(new_code, code)
        self.assertEqual(self.verify(verification_id, code).status_code, 400)
        self.assertEqual(self.verify(verification_id, new_code).status_code, 200)

    def test_code_is_bound_to_registration(self):
        verification_id, code = self.begin()
        response = self.client.post("/auth/register", json=dict(self.account, email="other@example.com"))
        other_id = response.json()["verification_id"]
        with self.app.state.database.connect() as connection:
            row = connection.execute("SELECT * FROM pending_registrations WHERE verification_id = ?", (other_id,)).fetchone()
            self.assertNotEqual(row["code_hash"], self.app.state.verification.hash_code(verification_id, self.last_code()))
        self.assertEqual(self.verify("A" * 43, code).status_code, 400)

    def test_pending_code_survives_restart(self):
        verification_id, code = self.begin()
        with TestClient(create_app(self.settings)) as restarted:
            response = restarted.post("/auth/verify-email", json={"verification_id": verification_id, "code": code})
            self.assertEqual(response.status_code, 200, response.text)

    def test_send_failure_rolls_back_registration(self):
        with patch.object(self.app.state.mailer, "send_verification", side_effect=MailDeliveryError("private SMTP error")):
            response = self.client.post("/auth/register", json=self.account)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private SMTP", response.text)
        with self.app.state.database.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM pending_registrations").fetchone()[0], 0)

    def test_send_failure_keeps_old_code(self):
        verification_id, code = self.begin()
        self.enable_resend()
        with patch.object(self.app.state.mailer, "send_verification", side_effect=MailDeliveryError("failed")):
            response = self.client.post("/auth/resend-verification", json={"verification_id": verification_id})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.verify(verification_id, code).status_code, 200)

    def test_register_again_invalidates_old_request(self):
        old_id, old_code = self.begin()
        self.enable_resend()
        response = self.client.post("/auth/register", json=dict(self.account, password="another-password"))
        self.assertEqual(response.status_code, 202)
        self.assertNotEqual(response.json()["verification_id"], old_id)
        self.assertEqual(self.verify(old_id, old_code).status_code, 400)
        self.assertEqual(self.verify(response.json()["verification_id"], self.last_code()).status_code, 200)
        self.client.post("/auth/logout")
        login = self.client.post("/auth/login", json={"email": self.account["email"], "password": self.account["password"]})
        self.assertEqual(login.status_code, 401)

    def test_hourly_email_limit_is_shared_between_register_and_resend(self):
        limited = create_app(replace(self.settings, verification_email_limit=2))
        with TestClient(limited) as client:
            response = client.post("/auth/register", json=self.account)
            verification_id = response.json()["verification_id"]
            self.enable_resend()
            self.assertEqual(client.post("/auth/resend-verification", json={"verification_id": verification_id}).status_code, 202)
            self.enable_resend()
            self.assertEqual(client.post("/auth/resend-verification", json={"verification_id": verification_id}).status_code, 429)

    def test_legacy_account_requires_confirmation_without_losing_password(self):
        with self.app.state.database.connect() as connection:
            connection.execute("INSERT INTO users (id, email, name, password_hash, created_at) VALUES (?, ?, ?, ?, ?)", ("legacy-user", self.account["email"], self.account["name"], self.app.state.password_hash.hash(self.account["password"]), "2026-11-07T00:00:00Z"))
        response = self.client.post("/auth/login", json={"email": self.account["email"], "password": self.account["password"]})
        self.assertEqual(response.status_code, 202, response.text)
        confirmed = self.verify(response.json()["verification_id"], self.last_code())
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["user"]["id"], "legacy-user")

    def test_production_disallows_file_mail_and_missing_secret(self):
        with self.assertRaises(ValueError):
            replace(self.settings, production=True, cookie_secure=True)

    def test_migration_preserves_old_account_and_revokes_old_session(self):
        old_path = self.settings.database_path.parent / "old.sqlite3"
        with sqlite3.connect(str(old_path)) as connection:
            connection.executescript("""
                CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires_at INTEGER NOT NULL);
            """)
            connection.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?)", ("old-id", self.account["email"], self.account["name"], self.app.state.password_hash.hash(self.account["password"]), "2026-11-07T00:00:00Z"))
            connection.execute("INSERT INTO sessions VALUES (?, ?, ?)", (hashlib.sha256(b"old-token").hexdigest(), "old-id", int(time.time()) + 1000))
        with TestClient(create_app(replace(self.settings, database_path=old_path))) as client:
            self.assertEqual(client.get("/auth/me", headers={"Cookie": "a1_session=old-token"}).status_code, 401)
            response = client.post("/auth/login", json={"email": self.account["email"], "password": self.account["password"]})
            self.assertEqual(response.status_code, 202, response.text)
            response = client.post("/auth/verify-email", json={"verification_id": response.json()["verification_id"], "code": self.last_code()})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["user"]["id"], "old-id")

    def test_smtp_ssl_sends_without_network(self):
        settings = replace(self.settings, mail_backend="smtp", smtp_host="smtp.example.com", smtp_username="sender", smtp_password="secret-password", mail_from="sender@example.com")
        with patch("backend.app.mailer.smtplib.SMTP_SSL") as smtp:
            smtp.return_value.send_message.return_value = {}
            Mailer(settings).send_verification("recipient@example.com", "012345")
            smtp.assert_called_once()
            smtp.return_value.login.assert_called_once_with("sender", "secret-password")
            message = smtp.return_value.send_message.call_args[0][0]
            self.assertIn("012345", message.get_content())

    def test_smtp_starttls_sends_without_network(self):
        settings = replace(self.settings, mail_backend="smtp", smtp_host="smtp.example.com", smtp_port=587, smtp_security="starttls", smtp_username="sender", smtp_password="secret-password", mail_from="sender@example.com")
        with patch("backend.app.mailer.smtplib.SMTP") as smtp:
            smtp.return_value.send_message.return_value = {}
            Mailer(settings).send_verification("recipient@example.com", "012345")
            smtp.return_value.starttls.assert_called_once()
            smtp.return_value.login.assert_called_once()


# unittest иначе повторно обнаружит импортированный набор AuthTests.
del _AuthTests
