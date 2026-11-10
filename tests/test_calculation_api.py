from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import secrets
import tempfile
import time
import unittest
import uuid

from fastapi.testclient import TestClient

from tests import BACKEND_DIR

from backend.app.config import Settings
from backend.app.main import create_app, digest


class CalculationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        settings = Settings(database_path=Path(self.temp.name) / "auth.sqlite3")
        self.app = create_app(settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        # Подтверждённый пользователь только в изолированной тестовой базе.
        now = datetime.now(timezone.utc).isoformat()
        token = secrets.token_urlsafe(32)
        with self.app.state.database.connect() as connection:
            connection.execute("INSERT INTO users (id,email,name,password_hash,created_at,email_verified_at) VALUES (?,?,?,?,?,?)", ("calc-test", "calc@example.com", "Проверка", self.app.state.dummy_hash, now, now))
            connection.execute("INSERT INTO sessions VALUES (?,?,?)", (digest(token), "calc-test", int(time.time()) + 3600))
        self.client.cookies.set("a1_session", token)
        self.example_dir = BACKEND_DIR / "examples"
        self.request = json.loads((self.example_dir / "calculation-request.json").read_text(encoding="utf-8"))

    def test_guest_can_calculate(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.post("/calculations", json=self.request).status_code, 200)
        self.assertEqual(self.client.get("/calculation-options").status_code, 200)

    def test_calculation_and_options_contract(self):
        response = self.client.post("/calculations", json=self.request)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["recommendation"], "contract_road")
        self.assertEqual(result["total_volume_tonnes"], 80)
        self.assertEqual(result["data_status"], "synthetic")
        self.assertEqual(result["schemes"][1]["total_cost_rub"], 208000)
        self.assertNotIn("password", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        options = self.client.get("/calculation-options")
        self.assertEqual(options.status_code, 200)
        self.assertEqual(options.json()["schedule_modes"], ["once", "regular", "explicit"])
        self.assertIn("Калуга", options.json()["cities"])

    def test_once_and_multi_stop_payload(self):
        request = json.loads((self.example_dir / "calculation-stops.json").read_text(encoding="utf-8"))
        response = self.client.post("/calculations", json=request)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["shipments"], 1)
        self.assertEqual(result["schemes"][0]["total_cost_rub"], 35000)
        self.assertEqual(result["schemes"][2]["status"], "insufficient_data")
        self.assertIsNone(result["schemes"][2]["total_cost_rub"])

    def test_unsupported_fields_and_client_tariffs_are_rejected(self):
        for extra in ({"stops": ["Калуга"]}, {"rate_rub": 1}, {"tariffs": {"spot_road": 1}}):
            with self.subTest(extra=list(extra)):
                response = self.client.post("/calculations", json=dict(self.request, **extra))
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "validation_error")
        self.request["route"][0]["unknown_wait"] = 20
        self.assertEqual(self.client.post("/calculations", json=self.request).status_code, 422)

    def test_incorrect_units_and_missing_volume(self):
        self.request["max_delivery_hours"] = "2 дня"
        self.assertEqual(self.client.post("/calculations", json=self.request).status_code, 422)
        self.request["max_delivery_hours"] = 48
        self.request["capacity_basis"] = "mass_and_volume"
        self.assertEqual(self.client.post("/calculations", json=self.request).status_code, 422)

    def test_unknown_dataset_and_too_many_events(self):
        response = self.client.post("/calculations", json=dict(self.request, dataset_id="not-existing"))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "calculation_error")
        self.request["schedule"]["frequency"] = {"count": 168, "unit": "day"}
        self.assertEqual(self.client.post("/calculations", json=self.request).status_code, 422)

    def test_no_solution_is_valid_result_not_server_error(self):
        response = self.client.post("/calculations", json=dict(self.request, max_delivery_hours=0.25))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["recommendation"])

    def test_cors_origin_and_json_requirements(self):
        origin = "http://localhost:5173"
        preflight = self.client.options("/calculations", headers={"Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
        self.assertEqual(preflight.status_code, 200)
        response = self.client.post("/calculations", json=self.request, headers={"Origin": origin})
        self.assertEqual(response.headers["access-control-allow-origin"], origin)
        self.assertEqual(response.headers["access-control-allow-credentials"], "true")
        self.assertEqual(self.client.post("/calculations", json=self.request, headers={"Origin": "https://foreign.example"}).status_code, 403)
        self.assertEqual(self.client.post("/calculations", data={"payload": "not-json"}).status_code, 415)

    def test_limit_is_per_user_and_recovers(self):
        for _ in range(30):
            self.assertEqual(self.client.post("/calculations", json=self.request).status_code, 200)
        blocked = self.client.post("/calculations", json=self.request)
        self.assertEqual(blocked.status_code, 429)
        self.assertGreater(int(blocked.headers["retry-after"]), 0)
        with self.app.state.database.connect() as connection:
            connection.execute("UPDATE auth_limits SET expires_at = 0 WHERE bucket = 'calculate:calc-test'")
        self.assertEqual(self.client.post("/calculations", json=self.request).status_code, 200)

    def test_openapi_has_calculation_contract(self):
        spec = self.client.get("/openapi.json").json()
        self.assertIn("/calculations", spec["paths"])
        self.assertIn("/calculation-options", spec["paths"])
        model = spec["components"]["schemas"]["CalculationRequest"]
        self.assertFalse(model["additionalProperties"])
        self.assertIn("capacity_basis", model["required"])
        self.assertIn("schedule", model["required"])


    def test_guest_forecast_and_private_history(self):
        snapshot = {
            "id": str(uuid.uuid4()), "createdAt": "2026-11-09T12:00:00Z",
            "result": {"request": self.request}, "draft": {}, "cargoNames": {},
        }
        body = {"account_id": "calc-test", "snapshot": snapshot}
        with TestClient(self.app) as guest:
            options = guest.get("/network/forecast/options")
            self.assertEqual(options.status_code, 200)
            route_id = options.json()["routes"][0]["route_id"]
            self.assertEqual(guest.get("/network/forecast", params={
                "route_id": route_id, "start_year": 2027, "end_year": 2028,
            }).status_code, 200)
            self.assertEqual(guest.get("/auth/history").status_code, 401)
            self.assertEqual(guest.post("/auth/history", json=body).status_code, 401)

        self.assertEqual(self.client.post("/auth/history", json=body).status_code, 204)
        self.assertEqual(self.client.post("/auth/history", json=body).status_code, 204)
        with self.app.state.database.connect() as connection:
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                "INSERT INTO users (id,email,name,password_hash,created_at,email_verified_at) VALUES (?,?,?,?,?,?)",
                ("other", "other@example.com", "Другой", self.app.state.dummy_hash, now, now),
            )
            for user_id in ("calc-test", "other"):
                connection.execute("INSERT INTO sessions VALUES (?,?,?)", (
                    digest("device-" + user_id), user_id, int(time.time()) + 3600,
                ))
        with TestClient(self.app) as phone:
            phone.cookies.set("a1_session", "device-calc-test")
            self.assertEqual(phone.get("/auth/history").json()["entries"], [snapshot])
            phone.cookies.set("a1_session", "device-other")
            self.assertEqual(phone.get("/auth/history").json()["entries"], [])
            self.assertEqual(phone.post("/auth/history", json=body).status_code, 409)

    def test_forecast_granularities_match(self):
        options = self.client.get("/network/forecast/options").json()
        query = {"route_id": options["routes"][0]["route_id"],
                 "start_year": 2027, "end_year": 2030}
        monthly = self.client.get("/network/forecast", params={**query, "granularity": "month"})
        self.assertEqual(monthly.status_code, 200, monthly.text)
        monthly = monthly.json()
        self.assertEqual(len(monthly["periods"]), 48)
        values = [Decimal(str(row["forecast_tonnes"])) for row in monthly["periods"]]
        for granularity, size in (("quarter", 3), ("year", 12)):
            with self.subTest(granularity=granularity):
                response = self.client.get("/network/forecast", params={**query, "granularity": granularity})
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertEqual(result["forecast_tonnes"], monthly["forecast_tonnes"])
                self.assertEqual(
                    [Decimal(str(row["forecast_tonnes"])) for row in result["periods"]],
                    [sum(values[i:i + size]) for i in range(0, len(values), size)],
                )

    def test_forecast_rejects_reversed_years(self):
        options = self.client.get("/network/forecast/options").json()
        response = self.client.get("/network/forecast", params={
            "route_id": options["routes"][0]["route_id"],
            "start_year": 2030,
            "end_year": 2027,
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "invalid_forecast_period")


if __name__ == "__main__":
    unittest.main()
