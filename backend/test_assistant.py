from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import tempfile
import time
import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient

from backend.app.assistant.schemas import ChatRequest, DeliveryDraft, DeliveryTurn, InformationTurn
from backend.app.assistant.service import handle_chat
from backend.app.config import Settings
from backend.app.main import create_app, digest


class AssistantServiceTests(unittest.TestCase):
    def test_partial_draft_lists_missing_fields_without_calculation(self):
        draft = DeliveryDraft(
            origin="Москва",
            destination="Брянск",
            cargo_type="flour",
            weight_tonnes=10,
            splittable=False,
            schedule={"mode": "regular"},
            max_delivery_hours=48,
            capacity_basis="mass_and_volume",
        )
        planner = Mock(return_value=DeliveryTurn(draft=draft))
        calculate = Mock()
        message = ChatRequest(message="Посчитай регулярную доставку")

        response = handle_chat(message, plan_turn=planner, dataset_id="test", calculate=calculate)

        self.assertEqual(response.kind, "clarification")
        self.assertEqual(response.draft, draft)
        self.assertEqual(
            response.missing_fields,
            ["schedule.start_at", "schedule.end_at", "schedule.frequency", "volume_m3"],
        )
        self.assertIsNone(response.calculation)
        planner.assert_called_once_with(message)
        calculate.assert_not_called()


class AssistantApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(database_path=Path(self.temp.name) / "auth.sqlite3")
        self.planner = Mock()
        self.app = create_app(self.settings, assistant_planner=self.planner)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc).isoformat()
        with self.app.state.database.connect() as connection:
            connection.execute(
                "INSERT INTO users (id,email,name,password_hash,created_at,email_verified_at) VALUES (?,?,?,?,?,?)",
                ("chat-test", "chat@example.com", "Проверка", self.app.state.dummy_hash, now, now),
            )
            connection.execute(
                "INSERT INTO sessions VALUES (?,?,?)", (digest(self.token), "chat-test", int(time.time()) + 3600)
            )
        self.client.cookies.set(self.settings.cookie_name, self.token)
        example = Path(__file__).resolve().parent / "examples" / "calculation-request.json"
        self.form = json.loads(example.read_text(encoding="utf-8"))
        self.draft = DeliveryDraft(
            origin=self.form["route"][0]["city"],
            destination=self.form["route"][1]["city"],
            cargo_type=self.form["cargoes"][0]["cargo_type"],
            weight_tonnes=self.form["cargoes"][0]["weight_tonnes"],
            splittable=self.form["cargoes"][0]["splittable"],
            schedule=self.form["schedule"],
            max_delivery_hours=self.form["max_delivery_hours"],
            capacity_basis=self.form["capacity_basis"],
        )

    def test_calculation_matches_form_and_accepts_updated_draft(self):
        self.planner.side_effect = lambda message: DeliveryTurn(draft=message.draft or self.draft)
        response = self.client.post("/assistant/chat", json={"message": "Посчитай доставку"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        chat = response.json()
        self.assertEqual(chat["kind"], "calculation")
        self.assertIn("синтетических", chat["reply"])
        self.assertEqual(chat["calculation"], self.client.post("/calculations", json=self.form).json())
        self.assertEqual(chat["calculation"]["recommendation"], "contract_road")
        self.planner.assert_called_once()

        draft = dict(chat["draft"], max_delivery_hours=168)
        response = self.client.post("/assistant/chat", json={"message": "До семи дней", "draft": draft})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["draft"], draft)
        self.assertEqual(result["calculation"]["total_volume_tonnes"], 80)
        self.assertEqual(result["calculation"]["recommendation"], "road_rail_road")

    def test_clarification_can_return_a_partial_draft(self):
        self.planner.return_value = DeliveryTurn(draft=DeliveryDraft(origin="Москва", weight_tonnes=10))
        response = self.client.post("/assistant/chat", json={"message": "Из Москвы, 10 тонн"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["kind"], "clarification")
        self.assertIn("destination", response.json()["missing_fields"])
        self.assertEqual(response.json()["draft"]["weight_tonnes"], 10)
        self.assertIsNone(response.json()["calculation"])

    def test_information_retains_draft_and_returns_source(self):
        self.planner.return_value = InformationTurn(
            reply="Расчёт учитывает накопление и перевалку.",
            sources=[{"title": "Методика", "path": "backend/CALCULATIONS.md", "section": "Ж/д"}],
        )
        draft = self.draft.model_dump(mode="json")
        response = self.client.post("/assistant/chat", json={"message": "Откуда формула?", "draft": draft})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["kind"], "information")
        self.assertEqual(result["draft"], draft)
        self.assertEqual(result["sources"][0]["path"], "backend/CALCULATIONS.md")
        self.assertEqual(result["missing_fields"], [])
        self.assertIsNone(result["calculation"])

    def test_auth_origin_and_json_checks_run_before_planner(self):
        blocked = self.client.post("/assistant/chat", json={"message": "Тест"}, headers={"Origin": "https://foreign.example"})
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.headers["cache-control"], "no-store")
        wrong_type = self.client.post("/assistant/chat", data={"message": "Тест"})
        self.assertEqual(wrong_type.status_code, 415)
        self.client.cookies.clear()
        unauthorized = self.client.post("/assistant/chat", json={"message": "Тест"})
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(unauthorized.headers["cache-control"], "no-store")
        self.planner.assert_not_called()

    def test_invalid_message_and_draft_are_rejected(self):
        for body in (
            {"message": "  "},
            {"message": "а" * 2001},
            {"message": "Тест", "draft": {"weight_tonnes": -1}},
            {"message": "Тест", "draft": {"weight_tonnes": "10"}},
            {"message": "Тест", "draft": {"tariffs": {"spot_road": 1}}},
            {"message": "Тест", "draft": {"schedule": {"ready_at": "2026-11-09T00:00:00"}}},
        ):
            with self.subTest(body=body):
                response = self.client.post("/assistant/chat", json=body)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "validation_error")
        self.planner.assert_not_called()

    def test_complete_draft_still_passes_calculation_validation(self):
        draft = self.draft.model_copy(update={"destination": self.draft.origin})
        self.planner.return_value = DeliveryTurn(draft=draft)
        response = self.client.post("/assistant/chat", json={"message": "Посчитай"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation_error")

    def test_chat_shares_calculation_quota_with_form(self):
        self.planner.return_value = DeliveryTurn(draft=self.draft)
        with self.app.state.database.connect() as connection:
            connection.execute("INSERT INTO auth_limits VALUES (?, ?, ?)", ("calculate:chat-test", 29, int(time.time()) + 60))

        self.assertEqual(self.client.post("/assistant/chat", json={"message": "Посчитай"}).status_code, 200)
        blocked = self.client.post("/calculations", json=self.form)
        self.assertEqual(blocked.status_code, 429)
        self.assertGreater(int(blocked.headers["retry-after"]), 0)

    def test_timeout_and_invalid_turn_use_public_error_format(self):
        self.planner.side_effect = TimeoutError("internal provider details")
        unavailable = self.client.post("/assistant/chat", json={"message": "Посчитай"})
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(unavailable.json()["error"]["code"], "assistant_unavailable")
        self.assertNotIn("internal provider details", unavailable.text)

        self.planner.side_effect = None
        self.planner.return_value = {"tool": "unknown"}
        invalid = self.client.post("/assistant/chat", json={"message": "Посчитай"})
        self.assertEqual(invalid.status_code, 502)
        self.assertEqual(invalid.json()["error"]["code"], "assistant_invalid_response")

    def test_app_without_model_keeps_form_available(self):
        with TestClient(create_app(self.settings)) as client:
            client.cookies.set(self.settings.cookie_name, self.token)
            self.assertEqual(client.get("/health").status_code, 200)
            response = client.post("/assistant/chat", json={"message": "Посчитай"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "assistant_unavailable")
            self.assertEqual(client.post("/calculations", json=self.form).status_code, 200)

    def test_openapi_lists_all_reply_variants(self):
        spec = self.client.get("/openapi.json").json()
        schema = spec["paths"]["/assistant/chat"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertEqual(set(schema["discriminator"]["mapping"]), {"clarification", "calculation", "information"})
