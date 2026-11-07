from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app, digest


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(database_path=Path(self.temp.name) / "auth.sqlite3")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.account = {"name": "Андрей", "email": "andrey@example.com", "password": "demo-password-2026"}

    def register(self):
        response = self.client.post("/auth/register", json=self.account)
        self.assertEqual(response.status_code, 201, response.text)
        return response

    def test_register_me_logout_and_revocation(self):
        response = self.register()
        user = response.json()["user"]
        self.assertEqual(user["name"], "Андрей")
        self.assertEqual(set(user), {"id", "name", "email", "created_at"})
        cookie = response.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)
        token = self.client.cookies.get("a1_session")
        self.assertEqual(self.client.get("/auth/me").json()["user"], user)
        self.assertEqual(self.client.post("/auth/logout").status_code, 204)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)
        replay = self.client.get("/auth/me", headers={"Cookie": "a1_session=" + token})
        self.assertEqual(replay.status_code, 401)
        self.assertEqual(self.client.post("/auth/logout").status_code, 204)

    def test_login_normalizes_email_and_rotates_session(self):
        self.register()
        old = self.client.cookies.get("a1_session")
        response = self.client.post("/auth/login", json={"email": " ANDREY@EXAMPLE.COM ", "password": self.account["password"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotEqual(old, self.client.cookies.get("a1_session"))
        self.assertEqual(self.client.get("/auth/me", headers={"Cookie": "a1_session=" + old}).status_code, 401)

    def test_unknown_email_and_wrong_password_have_same_error(self):
        self.register()
        responses = [self.client.post("/auth/login", json={"email": email, "password": "wrong-password"}) for email in ("andrey@example.com", "unknown@example.com")]
        self.assertEqual([r.status_code for r in responses], [401, 401])
        self.assertEqual(responses[0].json(), responses[1].json())

    def test_duplicate_email_is_case_insensitive(self):
        self.register()
        response = self.client.post("/auth/register", json=dict(self.account, email="ANDREY@example.com"))
        self.assertEqual(response.status_code, 409)

    def test_validation_does_not_echo_password(self):
        for fields in ({"password": "short"}, {"email": "invalid"}, {"name": "  "}, {"password": "x" * 129}):
            with self.subTest(fields=list(fields)):
                payload = dict(self.account, **fields)
                response = self.client.post("/auth/register", json=payload)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn(payload["password"], response.text)
                self.assertEqual(response.json()["error"]["code"], "validation_error")

    def test_expired_and_forged_sessions(self):
        self.assertEqual(self.client.get("/auth/me").status_code, 401)
        self.assertEqual(self.client.get("/auth/me", headers={"Cookie": "a1_session=forged"}).status_code, 401)
        self.register()
        with self.app.state.database.connect() as connection:
            connection.execute("UPDATE sessions SET expires_at = ?", (int(time.time()) - 1,))
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_password_and_session_are_hashed_in_database(self):
        self.register()
        token = self.client.cookies.get("a1_session")
        with self.app.state.database.connect() as connection:
            user = connection.execute("SELECT * FROM users").fetchone()
            session = connection.execute("SELECT * FROM sessions").fetchone()
        self.assertTrue(user["password_hash"].startswith("$argon2id$"))
        self.assertNotIn(self.account["password"], user["password_hash"])
        self.assertEqual(session["token_hash"], digest(token))
        self.assertNotEqual(session["token_hash"], token)

    def test_users_and_sessions_survive_app_restart(self):
        self.register()
        token = self.client.cookies.get("a1_session")
        with TestClient(create_app(self.settings)) as restarted:
            response = restarted.get("/auth/me", headers={"Cookie": "a1_session=" + token})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["user"]["email"], self.account["email"])

    def test_sessions_of_different_users_are_independent(self):
        self.register()
        first_token = self.client.cookies.get("a1_session")
        self.client.cookies.clear()
        self.client.post("/auth/register", json=dict(self.account, email="dasha@example.com", name="Даша"))
        self.assertEqual(self.client.get("/auth/me").json()["user"]["email"], "dasha@example.com")
        self.client.post("/auth/logout")
        response = self.client.get("/auth/me", headers={"Cookie": "a1_session=" + first_token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["email"], "andrey@example.com")

    def test_cors_and_foreign_origin(self):
        origin = "http://localhost:5173"
        preflight = self.client.options("/auth/login", headers={"Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
        self.assertEqual(preflight.status_code, 200)
        self.assertEqual(preflight.headers["access-control-allow-origin"], origin)
        self.assertEqual(preflight.headers["access-control-allow-credentials"], "true")
        for path in ("/auth/register", "/auth/login", "/auth/logout"):
            with self.subTest(path=path):
                response = self.client.post(path, json=self.account, headers={"Origin": "https://foreign.example"})
                self.assertEqual(response.status_code, 403)
        self.register()
        forbidden = self.client.post("/auth/logout", headers={"Origin": "https://foreign.example"})
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(self.client.get("/auth/me").status_code, 200)

    def test_allowed_origin_receives_error_and_no_cache(self):
        response = self.client.post("/auth/login", json={"email": self.account["email"], "password": "wrong-password"}, headers={"Origin": "http://localhost:5173"})
        self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:5173")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_form_body_rejected(self):
        response = self.client.post("/auth/login", data={"email": self.account["email"], "password": self.account["password"]})
        self.assertEqual(response.status_code, 415)

    def test_rate_limit_and_recovery(self):
        limited = create_app(replace(self.settings, login_limit=2, rate_window_seconds=60))
        with TestClient(limited) as client:
            credentials = {"email": "unknown@example.com", "password": "wrong-password"}
            self.assertEqual(client.post("/auth/login", json=credentials).status_code, 401)
            self.assertEqual(client.post("/auth/login", json=credentials).status_code, 401)
            blocked = client.post("/auth/login", json=credentials)
            self.assertEqual(blocked.status_code, 429)
            self.assertGreater(int(blocked.headers["retry-after"]), 0)
            with limited.state.database.connect() as connection:
                connection.execute("UPDATE auth_limits SET expires_at = ?", (int(time.time()) - 1,))
            self.assertEqual(client.post("/auth/login", json=credentials).status_code, 401)

    def test_secure_cookie(self):
        with TestClient(create_app(replace(self.settings, cookie_secure=True)), base_url="https://testserver") as client:
            response = client.post("/auth/register", json=self.account)
            self.assertEqual(response.status_code, 201)
            self.assertIn("Secure", response.headers["set-cookie"])
            self.assertEqual(client.get("/auth/me").status_code, 200)


if __name__ == "__main__":
    unittest.main()
