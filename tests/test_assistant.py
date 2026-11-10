from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from tests import BACKEND_DIR

from backend.app.assistant.ollama_client import OllamaClient
from backend.app.assistant.planner import DeliveryPlanner
from backend.app.assistant.schemas import ChatRequest, DeliveryDraft, DeliveryTurn, InformationTurn
from backend.app.assistant.service import handle_chat
from backend.app.assistant.tools import DOMAIN_TOOL_NAMES
from backend.app.config import Settings
from backend.app.main import create_app, digest
from backend.calculation_models import Schedule
from backend.schedule import build_schedule


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
        example = BACKEND_DIR / "examples" / "calculation-request.json"
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
            sources=[{"title": "Методика", "path": "docs/calculations/pilot/CALCULATIONS.md", "section": "Ж/д"}],
        )
        draft = self.draft.model_dump(mode="json")
        response = self.client.post("/assistant/chat", json={"message": "Откуда формула?", "draft": draft})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["kind"], "information")
        self.assertEqual(result["draft"], draft)
        self.assertEqual(result["sources"][0]["path"], "docs/calculations/pilot/CALCULATIONS.md")
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
        with patch.object(httpx.HTTPTransport, "handle_request", side_effect=httpx.ConnectError("offline")), \
                TestClient(create_app(self.settings)) as client:
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

    def connect_ollama(self, *provider_replies, route_tool=None):
        replies = iter(provider_replies)
        next_extraction = None

        def respond(request):
            nonlocal next_extraction
            self.assertEqual(request.url.path, "/api/chat")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "qwen3:8b")
            self.assertFalse(body["stream"])
            tool_names = [tool["function"]["name"] for tool in body["tools"]]
            self.assertNotIn("$ref", json.dumps(body["tools"]))
            self.assertNotIn("cookie", request.headers)
            if not tool_names:
                next_extraction = next(replies)
                function = next_extraction["tool_calls"][0]["function"]
                name = function["name"]
                selected = route_tool or (name if name in DOMAIN_TOOL_NAMES else "compare_delivery")
                route_input = json.loads(body["messages"][-1]["content"])
                if selected == "compare_delivery" and route_input["has_pilot_draft"] and function["arguments"].get("action") != "new":
                    selected = "resume_pilot"
                if function["arguments"].pop("continue_previous", False):
                    selected = "resume"
                self.assertIn(selected, body["format"]["properties"]["tool"]["enum"])
                result = {"content": json.dumps({"tool": selected})}
                self.assertIn("Брянск", body["messages"][-1]["content"])
            else:
                self.assertEqual(len(tool_names), 1)
                if tool_names == ["compare_delivery"]:
                    self.assertIn("Брянск", body["messages"][0]["content"])
                    self.assertIn("flour", body["messages"][0]["content"])
                result = next_extraction
            return httpx.Response(200, json={"done": True, "message": result})

        handler = Mock(side_effect=respond)
        http = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
        self.addCleanup(http.close)
        self.app.state.assistant_planner = DeliveryPlanner(
            OllamaClient(http, "qwen3:8b"), self.app.state.calculation_catalog, self.app.state.chukotavia,
            self.app.state.networks,
        )
        return handler

    @staticmethod
    def domain_reply(name, **arguments):
        return {"tool_calls": [{"function": {
            "name": name, "arguments": arguments,
        }}]}

    def tool_reply(self, action, **arguments):
        return self.domain_reply("compare_delivery", action=action, **arguments)

    def test_air_tariff_chat_uses_published_data_and_preserves_delivery_draft(self):
        handler = self.connect_ollama({"content": "Придуманная цена 999 ₽.", "tool_calls": [{"function": {
            "name": "lookup_air_freight_tariff",
            "arguments": {"origin": "Анадырь", "destination": "Провидения",
                          "flight_date": "2026-11-10", "weight_kg": 100, "volume_m3": 0.5},
        }}]})
        draft = self.draft.model_dump(mode="json")
        response = self.client.post("/assistant/chat", json={"message": "Цена ЧукотАВИА?", "draft": draft})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["kind"], "information")
        self.assertEqual(result["draft"], draft)
        self.assertIsNone(result["calculation"])
        self.assertIn("758,84 ₽/кг", result["reply"])
        self.assertIn("75 884 ₽", result["reply"])
        self.assertNotIn("999 ₽", result["reply"])
        self.assertEqual(result["sources"][0]["section"], "Страница 4, строка 155")
        self.assertEqual(result["sources"][0]["date"], "2026-11-01")
        self.assertIn("chukotavia.ru", result["sources"][0]["url"])
        self.assertEqual(result["result"]["details"]["transport_component_estimate_rub"], 75884)
        sent = json.loads(handler.call_args.args[0].content)
        self.assertNotIn("draft", json.loads(sent["messages"][-1]["content"]))

    def test_air_tariff_chat_does_not_extend_snapshot_to_another_year(self):
        self.connect_ollama({"tool_calls": [{"function": {
            "name": "lookup_air_freight_tariff",
            "arguments": {"origin": "Анадырь", "destination": "Провидения", "flight_date": "2027-11-10"},
        }}]})
        response = self.client.post("/assistant/chat", json={"message": "Тариф ЧукотАВИА в ноябре 2027?"})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertIn("не нашёл опубликованной ставки", result["reply"])
        self.assertEqual(result["result"]["status"], "unavailable")
        self.assertEqual(result["sources"][0]["path"], "backend/data_sources/chukotavia/dataset.json")

    def test_all_other_engines_return_the_same_payload_as_calculation_api(self):
        examples = BACKEND_DIR / "examples"
        forms = {
            engine: json.loads((examples / file).read_text(encoding="utf-8"))
            for engine, file in (("network", "river-request.json"), ("russia", "russia-request.json"),
                                 ("chukotavia", "chukotavia-request.json"))
        }
        forms["pipeline"] = {
            "engine": "pipeline", "route_id": "pipeline-taishet-meget", "net_weight_tonnes": 100,
            "shipment_at": "2026-11-09T00:00:00+03:00",
        }
        arguments = {engine: {key: value for key, value in form.items() if key != "engine"}
                     for engine, form in forms.items()}
        arguments["pipeline"] = {"origin": "Тайшет", "destination": "Мегет", "net_weight_tonnes": 100,
                                 "shipment_at": "2026-11-09"}
        self.connect_ollama(*(self.domain_reply("calculate_" + engine, **args) for engine, args in arguments.items()))
        for engine, form in forms.items():
            with self.subTest(engine=engine):
                reply = self.client.post("/assistant/chat", json={"message": f"Рассчитай по {engine}"})
                self.assertEqual(reply.status_code, 200, reply.text)
                result = reply.json()
                self.assertEqual(result["kind"], "calculation", result)
                self.assertEqual(result["engine"], engine)
                self.assertEqual(result["calculation"], self.client.post("/calculations", json=form).json())
                self.assertTrue(result["sources"])
                if engine == "pipeline":
                    self.assertIn("50 728 ₽ без НДС", result["reply"])
                    self.assertIsNone(result["calculation"]["total_cost_rub"])
                if engine == "chukotavia":
                    self.assertIn("75 884 ₽", result["reply"])
                    self.assertIsNone(result["calculation"]["overview"]["total_cost_rub"])

    def test_rail_request_uses_russia_when_model_selects_hub_catalog(self):
        form = json.loads((BACKEND_DIR / "examples/russia-request.json").read_text(encoding="utf-8"))
        arguments = {key: value for key, value in form.items() if key not in ("engine", "allowed_modes")}
        handler = self.connect_ollama(self.domain_reply("calculate_russia", **arguments),
                                      route_tool="calculate_network")
        response = self.client.post("/assistant/chat", json={
            "message": "Рассчитай доставку из Москвы в Брянск по железной дороге.",
            "pending": {"tool": "calculate_network", "arguments": {"network_id": "river-krasnoyarsk-dudinka-scenario-v1"}},
        })
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["kind"], "calculation")
        self.assertEqual(result["engine"], "russia")
        self.assertEqual(result["calculation"]["request"]["allowed_modes"], ["rail"])
        extraction = json.loads(handler.call_args.args[0].content)
        self.assertEqual(extraction["tools"][0]["function"]["name"], "calculate_russia")
        self.assertNotIn("pending", json.loads(extraction["messages"][-1]["content"]))

    def test_russia_comparison_asks_for_all_missing_inputs_and_resumes(self):
        self.connect_ollama(
            self.domain_reply("calculate_russia", origin="Брянск", destination="Москва",
                              allowed_modes=["road", "rail", "sea"],
                              capacity_basis="mass_only",
                              cargoes=[{"name": "Мука", "weight_tonnes": 10}]),
            self.domain_reply("calculate_russia", origin="Москва", destination="Новосибирск", max_delivery_hours=120,
                              schedule={"mode": "once", "ready_at": "2026-11-10T08:00:00+03:00"},
                              cargoes=[{"packaging": "bags", "volume_m3": 20, "splittable": True}]),
        )
        first = self.client.post("/assistant/chat", json={
            "message": "Как доставить 10 т муки из Брянска в Москву? Сравни чистую ж/д и мультимодальные варианты с авто и ж/д.",
        })
        self.assertEqual(first.status_code, 200, first.text)
        data = first.json()
        self.assertEqual(data["kind"], "clarification")
        self.assertEqual(set(data["missing_fields"]), {
            "schedule", "max_delivery_hours", "cargoes.0.packaging", "cargoes.0.volume_m3", "cargoes.0.splittable",
        })
        self.assertNotIn("cargoes", data["reply"])
        self.assertIn("когда груз будет готов", data["reply"])
        self.assertIn("можно ли разделить партию", data["reply"])
        self.assertEqual(data["pending"]["missing_fields"], data["missing_fields"])
        self.assertEqual(data["pending"]["arguments"]["allowed_modes"], ["road", "rail"])

        second = self.client.post("/assistant/chat", json={
            "message": "Разово, груз готов 12 ноября 2026 в 8 по Москве. До 120 часов, в мешках, 20 кубов, делить можно.",
            "pending": data["pending"],
        })
        self.assertEqual(second.status_code, 200, second.text)
        result = second.json()
        self.assertEqual(result["kind"], "calculation")
        self.assertEqual(result["calculation"]["request"]["cargoes"][0]["weight_tonnes"], 10)
        self.assertEqual(result["calculation"]["request"]["allowed_modes"], ["road", "rail"])
        self.assertEqual(result["calculation"]["request"]["origin"], "Брянск")
        self.assertEqual(result["calculation"]["request"]["destination"], "Москва")
        self.assertEqual(result["calculation"]["request"]["schedule"]["ready_at"], "2026-11-12T08:00:00+03:00")

    def test_russia_weekly_frequency_is_kept_when_ollama_omits_it(self):
        self.connect_ollama(self.domain_reply(
            "calculate_russia", origin="Москва", destination="Брянск", allowed_modes=["road"],
            cargoes=[{"name": "Мука", "weight_tonnes": 10, "packaging": "bags", "volume_m3": 20, "splittable": True}],
            schedule={"mode": "regular", "start_at": "2026-11-10T08:00:00+03:00"},
        ))
        response = self.client.post("/assistant/chat", json={"message": (
            "Из Москвы в Брянск. Отправляем по 10 т два раза в неделю в течение четырёх недель, всего 8 отправок. "
            "Первая партия будет готова 12 ноября 2026 года в 8:00 по Москве. "
            "Мука в мешках, объём одной партии 20 м³. Партию можно разделить."
        )})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["missing_fields"], ["max_delivery_hours"])
        schedule = data["pending"]["arguments"]["schedule"]
        self.assertEqual(schedule, {
            "mode": "regular", "start_at": "2026-11-12T08:00:00+03:00", "end_at": "2026-12-10T08:00:00+03:00",
            "frequency": {"count": 2, "every": 1, "unit": "week"},
        })
        events, _, _ = build_schedule(TypeAdapter(Schedule).validate_python(schedule))
        self.assertEqual(len(events), 8)

    def test_one_shipment_replaces_pending_regular_schedule(self):
        handler = self.connect_ollama(self.domain_reply(
            "calculate_russia", origin="Москва", destination="Брянск", allowed_modes=["road"],
            cargoes=[{"name": "Мука", "weight_tonnes": 80, "packaging": "bags", "volume_m3": 160, "splittable": True}],
            schedule={"mode": "regular", "start_at": "2026-11-12T08:00:00+03:00", "end_at": "2026-12-10T08:00:00+03:00"},
        ))
        first = self.client.post("/assistant/chat", json={"message": "Как лучше отправить муку из Москвы в Брянск?"})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertIn("schedule.frequency", first.json()["missing_fields"])
        response = self.client.post("/assistant/chat", json={
            "message": "одна отправка груза", "pending": first.json()["pending"],
        })
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["missing_fields"], ["max_delivery_hours"])
        self.assertEqual(data["pending"]["arguments"]["schedule"], {"mode": "once", "ready_at": "2026-11-12T08:00:00+03:00"})
        self.assertEqual(handler.call_count, 2)

    def test_russia_transport_in_2030_explains_coverage_before_asking_for_dates(self):
        self.connect_ollama(
            self.domain_reply("calculate_russia", origin="Брянск", destination="Москва",
                              cargoes=[{"name": "Мука", "weight_tonnes": 10}],
                              schedule={"mode": "regular", "start_at": "2030-01-01T00:00:00+03:00"}),
        )
        response = self.client.post("/assistant/chat", json={
            "message": "Как доставить 10 т муки из Брянска в Москву в 2030? Покажи варианты и скажи, какой выгоднее.",
        })
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["kind"], "clarification")
        self.assertIn("Для 2030 года", result["reply"])
        self.assertIn("на дату в 2026 году", result["reply"])
        self.assertNotIn("schedule", result["reply"])
        self.assertNotIn("schedule", result["pending"]["arguments"])
        follow_up = self.client.post("/assistant/chat", json={"message": "1 год", "pending": result["pending"]})
        self.assertEqual(follow_up.status_code, 200, follow_up.text)
        data = follow_up.json()
        self.assertEqual(data["pending"]["arguments"]["max_delivery_hours"], 8760)
        self.assertEqual(data["pending"]["arguments"]["origin"], "Брянск")
        self.assertNotIn("max_delivery_hours", data["missing_fields"])
        self.assertNotIn("schedule", data["pending"]["arguments"])

    def test_fesco_calculation_uses_stated_deadline_and_readiness_time(self):
        self.connect_ollama(self.domain_reply(
            "calculate_russia", origin="Владивосток", destination="Петропавловск-Камчатский", allowed_modes=["sea"],
            cargoes=[{"name": "Мука", "weight_tonnes": 10, "volume_m3": 20, "packaging": "bags", "splittable": True,
                      "max_delivery_hours": 120, "max_piece_tonnes": 0.025, "max_piece_dimensions_m": [0.5, 0.4, 0.2]}],
            schedule={"mode": "once", "ready_at": "2026-11-10T12:00:00+10:00"},
        ))
        response = self.client.post("/assistant/chat", json={"message": (
            "Рассчитай доставку Владивосток → Петропавловск-Камчатский морем. "
            "Груз готов 10 ноября 2026 года в 08:00 по Владивостоку, срок 14 дней."
        )})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["calculation"]
        self.assertEqual(data["request"]["max_delivery_hours"], 336)
        self.assertEqual(data["request"]["schedule"]["ready_at"], "2026-11-10T08:00:00+10:00")
        self.assertIsNotNone(data["recommendation"])
        self.assertIn("fesco-fpkl:2639PK", str(data["candidates"]))

    def test_river_calculation_keeps_explicit_cargo_properties(self):
        params = json.loads((BACKEND_DIR / "examples/river-request.json").read_bytes())
        params.pop("engine")
        cargo = params["cargoes"][0]
        cargo["handling_profile"] = cargo.pop("properties")["handling_profile"]
        self.connect_ollama(self.domain_reply("calculate_network", **params))
        response = self.client.post("/assistant/chat", json={"message": (
            "Рассчитай речную перевозку Красноярск → Дудинка. Груз сухой, твёрдый, неопасный, "
            "температурный режим не нужен."
        )})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["calculation"]
        winner = next(row for row in data["candidates"] if row["id"] == data["recommendation"])
        self.assertEqual(winner["total_cost_rub"], 166_200)

    def test_river_provider_named_in_chat_uses_its_published_package(self):
        params = json.loads((BACKEND_DIR / "examples/river-request.json").read_bytes())
        params.pop("engine")
        params["schedule"]["ready_at"] = "2026-06-17T08:00:00+07:00"
        self.connect_ollama(self.domain_reply("calculate_network", **params))
        response = self.client.post("/assistant/chat", json={"message": (
            "Рассчитай речную перевозку Красноярск → Дудинка у перевозчика Енисей-Норд."
        )})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["calculation"]
        winner = next(row for row in data["candidates"] if row["id"] == data["recommendation"])
        self.assertEqual(winner["total_cost_rub"], 180_000)
        self.assertIn("enisey-nord-2026", winner["source_ids"])

    def test_river_calculation_explains_expired_snapshot_without_extrapolating(self):
        params = json.loads((BACKEND_DIR / "examples/river-request.json").read_bytes())
        params.pop("engine")
        params["schedule"]["ready_at"] = "2030-06-08T08:00:00+07:00"
        params["cargoes"][0].update(weight_tonnes=25, volume_m3=40)
        reverse = dict(params, origin_hub_id=params["destination_hub_id"], destination_hub_id=params["origin_hub_id"])
        self.connect_ollama(self.domain_reply("calculate_network", **params), self.domain_reply("calculate_network", **reverse))
        response = self.client.post("/assistant/chat", json={
            "message": "Рассчитай речную перевозку 25 т книг Красноярск → Дудинка на 8 июня 2030 года.",
        })
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("Красноярск → Дудинка", body["reply"])
        self.assertIn("только навигацию 2026 года", body["reply"])
        self.assertIn("цену и срок рассчитать нельзя", body["reply"])
        self.assertIsNone(body["calculation"]["recommendation"])
        self.assertEqual(len(body["calculation"]["candidates"]), 2)
        for candidate in body["calculation"]["candidates"]:
            self.assertIsNone(candidate["total_cost_rub"])
            self.assertEqual(candidate["issues"][0]["code"], "expired_evidence")
        response = self.client.post("/assistant/chat", json={
            "message": "Рассчитай речную перевозку 25 т книг Дудинка → Красноярск на 8 июня 2030 года.",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Дудинка → Красноярск", response.json()["reply"])

    def test_river_request_to_another_city_does_not_substitute_destination(self):
        handler = self.connect_ollama()
        for message in ("Рассчитай речную перевозку Красноярск → Брянск.",
                        "Можно доставить книги по реке из Красноярска в Брянск?"):
            with self.subTest(message=message):
                response = self.client.post("/assistant/chat", json={"message": message})
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertIn("нет речного маршрута Красноярск → Брянск", body["reply"])
                self.assertIsNone(body["calculation"])
        handler.assert_not_called()

    def test_city_pair_without_mode_uses_general_calculator(self):
        self.connect_ollama(self.domain_reply("calculate_russia", origin="Красноярск", destination="Дудинка"),
                            route_tool="calculate_network")
        response = self.client.post("/assistant/chat", json={"message": "Рассчитай перевозку Красноярск → Брянск."})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["tool"], "calculate_russia")
        self.assertEqual(body["pending"]["arguments"]["destination"], "Брянск")

    def test_corridor_hypotheses_and_forecast_run_backend_formulas(self):
        self.connect_ollama(
            self.domain_reply("compare_corridor_hypotheses", origin="Москва", destination="Санкт-Петербург", year=2030),
            self.domain_reply("calculate_corridor_forecast", origin="Москва", destination="Санкт-Петербург", start_year=2030,
                              granularity="month"),
        )
        comparison = self.client.post("/assistant/chat", json={
            "message": "Проверь обе гипотезы для направления Москва — Санкт-Петербург на 2030 год.",
        })
        self.assertEqual(comparison.status_code, 200, comparison.text)
        body = comparison.json()
        self.assertEqual(body["tool"], "compare_corridor_hypotheses")
        self.assertEqual(body["result"]["details"]["origin"], "Москва")
        self.assertEqual(body["result"]["details"]["destination"], "Санкт-Петербург")
        self.assertEqual(body["result"]["details"]["deadline_days"], 14)
        self.assertIn("синтетические", body["reply"])
        self.assertEqual(len(body["result"]["rows"]), 3)
        self.assertIn("hypothesis_supported", body["result"]["rows"][1])
        self.assertEqual(len(body["sources"]), 3)
        forecast = self.client.post("/assistant/chat", json={
            "message": "Рассчитай прогноз грузопотока Москва — Санкт-Петербург по месяцам 2030 года.",
        })
        self.assertEqual(forecast.status_code, 200, forecast.text)
        series = forecast.json()["result"]
        self.assertEqual(len(series["rows"]), 12)
        self.assertEqual(series["details"]["volume_status"], "synthetic")
        self.assertEqual(series["details"]["forecast_tonnes"], body["result"]["details"]["forecast_tonnes"])

    def test_tool_clarifications_preserve_parameters_and_pilot_draft(self):
        self.connect_ollama(
            self.domain_reply("lookup_chukotavia_schedule", origin="Анадырь", destination="Провидения"),
            self.domain_reply("lookup_chukotavia_schedule", flight_date="2026-11-10", continue_previous=True),
            self.domain_reply("calculate_pipeline", origin="Тайшет", destination="Мегет", net_weight_tonnes=100),
            self.domain_reply("calculate_pipeline", shipment_at="2026-11-09", continue_previous=True),
        )
        draft = self.draft.model_dump(mode="json")
        for message, expected_field, followup, expected_kind in (
            ("Какие рейсы из Анадыря в Провидения?", "flight_date", "10 ноября 2026", "information"),
            ("Посчитай 100 т нетто нефти из Тайшета в Мегет", "shipment_at", "9 ноября 2026", "calculation"),
        ):
            response = self.client.post("/assistant/chat", json={"message": message, "draft": draft})
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result["kind"], "clarification")
            self.assertEqual(result["missing_fields"], [expected_field])
            self.assertEqual(result["draft"], draft)
            self.assertIsNone(result["calculation"])
            response = self.client.post("/assistant/chat", json={
                "message": followup, "draft": result["draft"], "pending": result["pending"],
            })
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result["kind"], expected_kind)
            self.assertEqual(result["draft"], draft)
            self.assertIsNone(result["pending"])
            if expected_kind == "information":
                self.assertTrue(result["result"]["rows"])
                self.assertFalse(result["result"]["rows"][0]["booking_confirmed"])

    def test_reference_tools_use_snapshot_rows_and_keep_evidence_scope(self):
        queries = [
            ("lookup_connectivity", {"origin": "Москва", "destination": "Брянск", "mode": "rail"}, "distance_km", 426),
            ("lookup_fesco_voyages", {"origin": "Владивосток", "date_from": "2026-10-27", "date_to": "2026-10-27"},
             "departure_date", "2026-10-27"),
            ("lookup_river_scenario", {"origin": "Красноярск", "destination": "Дудинка"},
             "scenario_travel_hours", 144),
            ("lookup_pipeline_reference", {"section": "routes", "origin": "Тайшет", "destination": "Мегет"},
             "rate_rub", "507.28"),
            ("lookup_rosmorport", {"query": "Большой порт Санкт-Петербург"}, "navigation_text", "Круглогодично"),
            ("lookup_asop", {"year": 2025, "period_kind": "Q1", "metric": "port_outbound", "category": "road"},
             "value_tonnes", 7_400_000),
            ("lookup_transport_strategy", {"year": 2030, "mode": "road", "scenario": "baseline"},
             "scope", "Russian_Federation_aggregate_not_city_corridor"),
            ("rank_regional_forecast", {"year": 2030, "transport": "road"}, None, None),
        ]
        self.connect_ollama(*(self.domain_reply(tool, **args) for tool, args, _, _ in queries))
        for tool, _, field, expected in queries:
            with self.subTest(tool=tool):
                response = self.client.post("/assistant/chat", json={"message": tool})
                self.assertEqual(response.status_code, 200, response.text)
                reply = response.json()
                self.assertEqual(reply["kind"], "information")
                self.assertEqual(reply["tool"], tool)
                self.assertTrue(reply["sources"])
                self.assertTrue(reply["result"]["assumptions"])
                if field:
                    self.assertEqual(reply["result"]["status"], "available")
                    self.assertIn(expected, [row[field] for row in reply["result"]["rows"]])
                else:
                    self.assertEqual(reply["result"]["status"], "unavailable")
                    self.assertEqual(reply["result"]["rows"], [])
                    self.assertIn("нет регионального прогноза", reply["reply"])

    def test_strategy_tool_combines_calls_for_both_scenarios(self):
        calls = [
            self.domain_reply("lookup_transport_strategy", year=2030, mode="road", scenario=scenario)["tool_calls"][0]
            for scenario in ("baseline", "conservative")
        ]
        self.connect_ollama({"tool_calls": calls})
        response = self.client.post("/assistant/chat", json={
            "message": "Покажи прогноз автоперевозок по России на 2030 год по Транспортной стратегии. Покажи оба сценария",
        })
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["kind"], "information")
        self.assertEqual(
            {row["scenario"]: row["value_million_tonnes"] for row in result["result"]["rows"]},
            {"baseline": 6117, "conservative": 5984},
        )
        self.assertIn("133 млн т", result["reply"])
        self.assertIn("2,2% от объёма в базовом сценарии", result["reply"])
        self.assertIn("более осторожная оценка", result["reply"])
        self.assertEqual(result["sources"][0]["date"], "2021-11-27")
        self.assertTrue(result["sources"][0]["url"].startswith("https://static.government.ru/"))

    def test_ollama_tool_clarifies_then_compares_and_recalculates(self):
        handler = self.connect_ollama(
            self.tool_reply("new", changes={
                "origin": "москва", "destination": "Брянск", "cargo_type": "Мука", "weight_tonnes": 10,
            }),
            self.tool_reply("update", changes={
                "splittable": True, "capacity_basis": "mass_only", "max_delivery_hours": 48,
                "schedule": {"mode": "regular", "start_at": "2026-11-09", "end_at": "2026-12-07",
                             "frequency": {"count": 2, "every": 1, "unit": "week"}},
            }),
            self.tool_reply("update", changes={"max_delivery_hours": 168}),
        )
        first = self.client.post("/assistant/chat", json={"message": "10 т муки Москва → Брянск"}).json()
        self.assertEqual(first["kind"], "clarification")
        self.assertEqual(first["draft"]["cargo_type"], "flour")
        self.assertIn("schedule.mode", first["missing_fields"])

        second = self.client.post("/assistant/chat", json={
            "message": "Дважды в неделю с 9 ноября до 7 декабря 2026, до 48 часов, делить можно, расчёт по массе",
            "draft": first["draft"],
        }).json()
        self.assertEqual(second["kind"], "calculation")
        self.assertEqual(second["calculation"], self.client.post("/calculations", json=self.form).json())
        self.assertIn("208 000 ₽", second["reply"])
        self.assertIn("UTC+03:00", second["reply"])
        self.assertIn("массе", second["reply"])

        third = self.client.post("/assistant/chat", json={
            "message": "А теперь до семи дней", "draft": second["draft"],
        }).json()
        self.assertEqual(third["draft"], dict(second["draft"], max_delivery_hours=168))
        self.assertEqual(third["calculation"]["total_volume_tonnes"], 80)
        self.assertEqual(third["calculation"]["recommendation"], "road_rail_road")
        self.assertIn("140 000 ₽", third["reply"])
        self.assertIn("156 ч", third["reply"])
        self.assertEqual(handler.call_count, 6)

    def test_new_and_changed_cargo_do_not_reuse_inapplicable_data(self):
        handler = self.connect_ollama(
            self.tool_reply("update", changes={"cargo_type": "Сахар"}),
            self.tool_reply("update", changes={"origin": "Калуга", "destination": "Брянск", "weight_tonnes": 2}),
        )
        draft = self.draft.model_dump(mode="json")
        changed = self.client.post("/assistant/chat", json={"message": "А если сахар?", "draft": draft}).json()
        self.assertEqual(changed["kind"], "clarification")
        self.assertEqual(changed["draft"]["cargo_type"], "sugar")
        self.assertEqual(changed["draft"]["schedule"], draft["schedule"])
        self.assertIsNone(changed["draft"]["weight_tonnes"])
        self.assertIsNone(changed["draft"]["splittable"])

        new = self.client.post("/assistant/chat", json={
            "message": "Новый расчёт. Калуга → Брянск, 2 т", "draft": draft,
        }).json()
        self.assertEqual(new["kind"], "clarification")
        self.assertEqual(new["draft"]["origin"], "Калуга")
        self.assertEqual(new["draft"]["weight_tonnes"], 2)
        self.assertIsNone(new["draft"]["schedule"])
        self.assertIsNone(new["draft"]["max_delivery_hours"])
        provider_body = json.loads(handler.call_args.args[0].content)
        self.assertNotIn("draft", json.loads(provider_body["messages"][-1]["content"]))

    def test_ambiguity_unknown_places_and_unsupported_modes_do_not_calculate(self):
        self.connect_ollama(
            self.tool_reply("clarify", question="Это новый расчёт или смена маршрута?"),
            self.tool_reply("update", changes={"destination": "Самара"}),
            self.tool_reply("unsupported", reason="transport_mode"),
            self.tool_reply("unsupported", reason="complex_delivery"),
        )
        draft = self.draft.model_dump(mode="json")
        for message, kind, fields in (
            ("Калуга — Брянск", "clarification", ["intent"]),
            ("А если до Самары?", "clarification", ["destination"]),
            ("А если самолётом?", "information", []),
            ("Добавь остановку в Калуге", "information", []),
        ):
            with self.subTest(message=message):
                result = self.client.post("/assistant/chat", json={"message": message, "draft": draft}).json()
                self.assertEqual(result["kind"], kind)
                self.assertEqual(result["missing_fields"], fields)
                self.assertIsNone(result["calculation"])
                self.assertEqual(result["draft"]["origin"], draft["origin"])

    def test_ollama_cannot_override_server_fields_or_request_more_tools(self):
        valid = self.tool_reply("update", changes={"max_delivery_hours": 168})
        self.connect_ollama(
            self.tool_reply("new", changes={"dataset_id": "untrusted"}),
            {"tool_calls": [{"function": {"name": "other_tool", "arguments": {}}}]},
            {"tool_calls": valid["tool_calls"] * 2},
        )
        draft = self.draft.model_dump(mode="json")
        for _ in range(3):
            response = self.client.post("/assistant/chat", json={"message": "Посчитай", "draft": draft})
            self.assertEqual(response.status_code, 502, response.text)
            self.assertEqual(response.json()["error"]["code"], "assistant_invalid_response")

    def test_once_requires_time_and_keeps_explicit_timezone(self):
        self.connect_ollama(
            self.tool_reply("update", changes={"schedule": {"mode": "once", "ready_at": "2026-11-09"}}),
            self.tool_reply("update", changes={"schedule": {"ready_at": "2026-11-09T10:00:00+04:00"}}),
        )
        first = self.client.post("/assistant/chat", json={
            "message": "Один рейс 9 ноября", "draft": self.draft.model_dump(mode="json"),
        }).json()
        self.assertEqual(first["kind"], "clarification")
        self.assertEqual(first["missing_fields"], ["schedule.ready_at"])
        self.assertIsNone(first["draft"]["schedule"]["frequency"])

        second = self.client.post("/assistant/chat", json={
            "message": "Груз готов в 10:00 UTC+4", "draft": first["draft"],
        }).json()
        self.assertEqual(second["kind"], "calculation")
        self.assertEqual(second["calculation"]["shipments"], 1)
        self.assertEqual(second["draft"]["schedule"]["ready_at"], "2026-11-09T10:00:00+04:00")
        self.assertIn("UTC+04:00", second["reply"])

    def test_chat_quota_is_checked_before_calling_ollama(self):
        with self.app.state.database.connect() as connection:
            connection.execute("INSERT INTO auth_limits VALUES (?, ?, ?)", ("assistant:chat-test", 30, int(time.time()) + 60))
        response = self.client.post("/assistant/chat", json={"message": "Посчитай"})
        self.assertEqual(response.status_code, 429, response.text)
        self.assertGreater(int(response.headers["retry-after"]), 0)
        self.planner.assert_not_called()
        self.assertEqual(self.client.post("/calculations", json=self.form).status_code, 200)
