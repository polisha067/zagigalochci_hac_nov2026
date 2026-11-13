import json
import os
import unittest
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
from uuid import uuid4
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from backend.a1_forecast import forecast_options
from backend.app.database import Database
from backend.app.assistant.dialog_models import DialogRequest
from backend.app.assistant.dialog_store import dialog_turn
from backend.app.assistant.ollama_client import OllamaMessage
from backend.app.assistant.planner import DeliveryPlanner
from backend.app.assistant.service import AssistantUnavailable
from backend.network.russia import load_russia
from backend.network.tlm_tariff import bundled_tariff
from backend.update_tlm_tariff import refresh
from tests import test_assistant


class AssistantDialogTests(unittest.TestCase):
    def setUp(self):
        case = test_assistant.AssistantApiTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        self.client = case.client
        self.app = case.app.state
        self.revision = 0
        self.model = Mock()
        self.app.assistant_planner = DeliveryPlanner(self.model, self.app.calculation_catalog, self.app.chukotavia, self.app.networks)

    def send(self, message, action=None, changes=None, *, evidence=None, turn_id=None, revision=None, status=200):
        if action:
            values = changes or {}

            def paths(row, path=""):
                if isinstance(row, list) and all(isinstance(item, dict) for item in row):
                    return [item for i, value in enumerate(row) for item in paths(value, f"{path}.{i}")]
                if isinstance(row, dict):
                    return [item for key, value in row.items()
                            for item in paths(value, f"{path}.{key}" if path else key)]
                return [path]

            self.model.chat.side_effect = None
            self.model.chat.return_value = OllamaMessage(content=json.dumps({
                "action": action, "changes": values,
                "evidence": evidence if evidence is not None else [{"path": path, "quote": message} for path in paths(values)],
            }))
        previous_calls = self.model.chat.call_count
        response = self.client.post("/assistant/dialog", json={
            "message": message, "turn_id": str(turn_id or uuid4()),
            "revision": self.revision if revision is None else revision,
        })
        self.assertEqual(response.status_code, status, response.text)
        if action:
            self.assertEqual(self.model.chat.call_count - previous_calls, 1)
        data = response.json()
        if status == 200:
            self.revision = data["revision"]
        return data

    def flour(self, *, destination="Нижний Новгород"):
        return self.send(
            f"10 т муки из Москвы в {destination} в 2030 году, делимые мешки, 20 м³.",
            "new", {"origin": "Москва", "destination": destination, "target_year": 2030,
                    "cargoes": [{"name": "Мука", "weight_tonnes": 10, "volume_m3": 20,
                                 "splittable": True, "packaging": "bags"}]},
        )

    def test_cheapest_new_route_with_typos_replaces_previous_route(self):
        self.flour()
        result = self.send("как дешевле перевзти 100 тонн муки из москвы во владисовток?",
                           "update", {"cargoes": [{"weight_tonnes": 100}]})
        self.assertIn("Москва → Владивосток", result["known"])
        self.assertNotIn("2030 год", result["known"])
        self.assertEqual(result["answer"]["kind"], "clarification")

    def test_new_forecast_compares_modes_even_when_planner_repeats_modes(self):
        dialog = self.send(
            "Новый расчёт. Сколько будет стоить перевезти 10 тонн муки из Москвы в Красноярск в 2030 году? "
            "Мука в мешках, общий объём 20 м³, партию можно разделить. "
            "Добавь обязательную остановку в Екатеринбурге на 2 часа. "
            "Сравни перевозку по железной дороге, на машине и смешанные варианты", "compare",
            {"allowed_modes": ["rail", "road", "rail", "road"]},
        )
        answer = dialog["answer"]
        self.assertEqual(answer["kind"], "calculation", answer)
        calculation = answer["calculation"]
        self.assertEqual(calculation["request"]["allowed_modes"], ["rail", "road"])
        self.assertTrue(any(row.get("connection_pilot") for row in calculation["candidates"]))
    def test_cubic_metres_followup_stays_in_delivery_draft(self):
        self.send(
            "Нужно перевезти 10 тонн муки из Москвы в Красноярск через Екатеринбург в 2030 году, срок до 5 дней",
            "new", {"origin": "Москва", "destination": "Красноярск", "target_year": 2030,
                    "cargoes": [{"name": "Мука", "weight_tonnes": 10}]},
        )
        answer = self.send(
            "в мешках, партию делим, объем груза 21 метр куб", "update",
            {"cargoes": [{"packaging": "bags", "splittable": True, "volume_m3": 21}]},
        )["answer"]
        cargo, = answer["calculation"]["request"]["cargoes"]
        self.assertEqual(cargo["volume_m3"], 21)
        self.assertEqual(cargo["weight_tonnes"], 10)
        self.assertEqual(answer["calculation"]["request"]["stops"][0]["settlement"], "Екатеринбург")
        self.assertEqual(answer["calculation"]["request"]["max_delivery_hours"], 120)

    def test_volume_forecast_uses_same_model_as_volume_screen(self):
        answer = self.send(
            "Построй прогноз объёма грузоперевозок из Челябинска в Омск на 2027–2035 годы."
        )["answer"]
        self.assertEqual(answer["tool"], "calculate_corridor_forecast")
        data = answer["result"]
        api = self.client.get("/network/forecast", params={
            "route_id": data["details"]["route_id"], "start_year": 2027, "end_year": 2035,
        })
        self.assertEqual(api.status_code, 200, api.text)
        self.assertEqual(data["rows"], api.json()["periods"])
        self.assertIn("синтетические", answer["reply"])
        updated = self.send("А если на 2030 год по месяцам?")["answer"]
        self.assertEqual(len(updated["result"]["rows"]), 12)
        self.assertEqual(updated["result"]["rows"][0]["period"], "2030-01")
        self.model.chat.assert_not_called()

    @patch.object(Database, "consume_attempt", return_value=None)
    def test_volume_forecast_all_directions_and_granularities(self, _rate_limit):
        for route in forecast_options()["routes"]:
            with self.subTest(route=route["route_id"]):
                origin, destination = route["origin"], route["destination"]
                for message, granularity, count in (
                    (f"Спрогнозируй объём перевозок {origin} {destination} с 2030 по 2031 год по годам", "year", 2),
                    ("А теперь покажи прогноз на 2030 год по месяцам.", "month", 12),
                    ("А теперь покажи прогноз на 2030 год по кварталам.", "quarter", 4),
                ):
                    answer = self.send(message)["answer"]
                    self.assertEqual(answer["tool"], "calculate_corridor_forecast", answer)
                    result = answer["result"]
                    self.assertEqual(result["details"]["route_id"], route["route_id"])
                    self.assertEqual(result["details"]["granularity"], granularity)
                    self.assertEqual(len(result["rows"]), count)
                    expected = self.client.get("/network/forecast", params={
                        "route_id": route["route_id"], "start_year": 2030,
                        "end_year": 2031 if granularity == "year" else 2030,
                        "granularity": granularity,
                    })
                    self.assertEqual(expected.status_code, 200, expected.text)
                    self.assertEqual(result["rows"], expected.json()["periods"])
        self.model.chat.assert_not_called()

    def test_short_volume_request_switches_from_pending_delivery(self):
        self.send(
            "Нужно перевезти 10 тонн муки из Москвы в Красноярск в 2030 году",
            "new", {"origin": "Москва", "destination": "Красноярск", "target_year": 2030,
                    "cargoes": [{"name": "Мука", "weight_tonnes": 10}]},
        )
        calls = self.model.chat.call_count
        unsupported = self.send("Скажи объем грузов в 2030 по направлению Москва Челябинск")
        self.assertIn("12 направлений", unsupported["answer"]["reply"])
        self.assertEqual(unsupported["known"], [])
        answer = self.send("Скажи объем грузов в 2030 по направлению Челябинск Омск")["answer"]
        self.assertEqual(answer["result"]["details"]["forecast_tonnes"], 1269.0)
        self.assertEqual(self.model.chat.call_count, calls)

    def test_volume_forecast_recognizes_cities_separated_by_space(self):
        answer = self.send("Прогнозируй объем с 2030 по 2035 года Челябинск Омск")["answer"]
        self.assertEqual(answer["tool"], "calculate_corridor_forecast")
        self.assertEqual(len(answer["result"]["rows"]), 6)
        self.model.chat.assert_not_called()

    def test_volume_forecast_clarifies_route_and_does_not_reverse_it(self):
        answer = self.send("Прогноз объёма Омск → Челябинск на 2030 год")["answer"]
        self.assertEqual(answer["kind"], "clarification")
        answer = self.send("Челябинск Омск")["answer"]
        self.assertEqual(answer["result"]["details"]["forecast_tonnes"], 1269.0)

    def test_air_forecast_matches_main_form(self):
        message = ("Новый расчёт. Москва → Якутск, 2030 год. Сколько стоит перевезти "
                   "100 кг муки в мешках по 25 кг? Общий объём 0,2 м³, партия делимая. "
                   "Размер одного мешка 0,5 × 0,4 × 0,2 м.")
        dialog = self.send(message, "new", {
            "origin": "Москва", "destination": "Якутск", "target_year": 2030,
            "cargoes": [{"name": "Мука", "weight_tonnes": .1, "volume_m3": .2, "packaging": "bags",
                         "splittable": True, "max_piece_tonnes": .025}],
        })
        answer = dialog["answer"]
        self.assertEqual(answer["kind"], "calculation", answer)
        calculation = answer["calculation"]
        self.assertEqual(calculation["request"]["cargoes"][0]["max_piece_dimensions_m"], [.5, .4, .2])
        row = next(row for row in calculation["candidates"] if row["modes"] == ["air"])
        self.assertIn("сценарий с ослаблением роста", answer["reply"])
        self.assertEqual(row["forecast_price"]["air_median_rub"], 36999.47)
        self.assertEqual(row["forecast_price"]["air_softened_rub"], 31603.64)
        form = self.client.post("/network/russia/forecasts", json=calculation["request"])
        self.assertEqual(form.status_code, 200, form.text)
        self.assertEqual(calculation, form.json())

    def test_road_market_estimate_matches_guest_form_and_respects_policy(self):
        cargo = {"name": "Мука", "weight_tonnes": 100, "volume_m3": 200,
                 "packaging": "bags", "splittable": True}
        message = ("Из Воронежа в Новокузнецк в 2030 году, только авто. "
                   "100 тонн муки в мешках, 200 м³, делимая партия.")
        answer = self.send(message, "new", {
            "origin": "Воронеж", "destination": "Новокузнецк", "target_year": 2030,
            "allowed_modes": ["road"], "cargoes": [cargo],
        })["answer"]
        self.assertEqual(answer["kind"], "calculation", answer)
        calculation = answer["calculation"]
        self.assertEqual(calculation["road_market_estimate"]["forecast_road_rub"], 597110.15)
        self.assertIn("Сценарная оценка основного автоплеча", answer["reply"])

        self.client.cookies.clear()
        form = self.client.post("/network/russia/forecasts", json=calculation["request"])
        self.assertEqual(form.status_code, 200, form.text)
        self.assertEqual(form.json()["road_market_estimate"], calculation["road_market_estimate"])
        self.assertEqual(form.json()["land_market"], calculation["land_market"])

        strict = calculation["request"] | {"data_policy": "verified_only"}
        form = self.client.post("/network/russia/forecasts", json=strict)
        self.assertEqual(form.status_code, 200, form.text)
        self.assertIsNone(form.json()["road_market_estimate"])
        self.assertIsNone(form.json()["land_market"])

    def test_show_all_routes_clears_air_only_restriction(self):
        first = self.send(
            "Новый расчёт. Москва → Якутск, 2031 год. Только прямое авиа. "
            "100 кг муки в мешках по 25 кг, размер одного мешка 50 × 40 × 20 см, общий объём 0,2 м³. "
            "Партию можно делить.",
            "new", {"allowed_modes": ["air"]},
        )["answer"]["calculation"]
        self.assertEqual([row["modes"] for row in first["candidates"]], [["air"]])
        calls = self.model.chat.call_count
        expanded = self.send("А ещё ж/д и авто по этому маршруту?")["answer"]["calculation"]
        self.assertEqual(self.model.chat.call_count, calls)
        self.assertEqual({key: value for key, value in expanded["request"].items() if key != "allowed_modes"},
                         {key: value for key, value in first["request"].items() if key != "allowed_modes"})
        self.assertIn(["air"], [row["modes"] for row in expanded["candidates"]])
        self.assertTrue(any(row["multimodal"] for row in expanded["candidates"]))

    def test_rail_stop_without_reloading_matches_main_form_and_persists(self):
        reply = self.send(
            "Новый расчёт. Москва → Новосибирск → Красноярск, 2030 год. "
            "Только ж/д. 10 т муки в мешках, 20 м³, партия делимая. "
            "Остановка в Новосибирске на 2 часа без погрузки и выгрузки.",
            "new", {"allowed_modes": ["rail"], "cargoes": [{
                "name": "Мука", "weight_tonnes": 10, "volume_m3": 20,
                "packaging": "bags", "splittable": True,
            }]},
        )["answer"]
        self.assertEqual(reply["kind"], "calculation", reply)
        result = reply["calculation"]
        stop, = result["request"]["stops"]
        self.assertEqual((stop["settlement"], stop["dwell_hours"], stop["no_reload"]), ("Новосибирск", 2, True))
        self.assertIsNone(result["recommendation"])
        candidate = result["candidates"][0]
        self.assertIsNone(candidate["total_cost_rub"])
        self.assertEqual(candidate["known_cost_subtotal_rub"], 262170.36)
        self.assertIn("262 170,36 ₽", reply["reply"])
        self.assertEqual(
            [edge["reference_metadata"]["container_size_ft"] for edge in candidate["connections"]],
            [40, 40],
        )
        self.assertEqual([(item["kind"], item["base_amount_rub"]) for item in candidate["cost_items"]],
                         [("transport", 81390), ("transport", 75000)])
        network = load_russia()
        form = result["request"] | {
            "origin": network.resolve(result["request"]["origin"]),
            "destination": network.resolve(result["request"]["destination"]),
            "stops": [stop | {"settlement": network.resolve(stop["settlement"])}],
        }
        self.assertEqual(self.client.post("/network/russia/forecasts", json=form).json(), result)
        updated = self.send("А если в 2028 году?")["answer"]["calculation"]
        self.assertEqual(updated["request"], result["request"] | {"target_year": 2028})
        self.assertLess(updated["candidates"][0]["known_cost_subtotal_rub"], candidate["known_cost_subtotal_rub"])

    def test_tlm_refresh_is_used_by_chat_and_form_after_effective_date(self):
        directory = Path(self.app.settings.database_path).parent / "tariffs"
        old = bundled_tariff()
        rows = []
        for city, price20, price40, security20, security40, days in old["rows"]:
            price20 = 50000 if city == "Новосибирск" else price20
            rows.append(f"<tr><td rowspan='2'>{escape(city)}</td><td>20-фут</td>"
                        f"<td>{price20}</td><td>{security20}</td><td rowspan='2'>{days}</td></tr>"
                        f"<tr><td>40-фут</td><td>{price40}</td><td>{security40}</td></tr>")
        html = f"""
        <h2>Тарифы с 01.02.2027 (От станции до станции)</h2>
        <table><tr><th>Пункт назначения</th><th>Тип</th><th>Стоимость (руб)</th>
        <th>Охрана (руб)</th><th>Срок (суток)</th></tr>{''.join(rows)}</table>
        <h4>В стоимость включено (НДС 22%):</h4>
        <ul><li>ЖД тариф и предоставление платформы.</li><li>Подача контейнера под погрузку.</li>
        <li>Погрузо-разгрузочные работы (терминал).</li><li>Дислокация и отслеживание.</li>
        <li>ЗПУ (пломбы).</li></ul>
        <h4>Важная информация:</h4>
        <ul><li>Простой на погрузке: свыше 3ч (20фт) / 4ч (40фт) — 1000 руб/час.</li>
        <li>Сверхнорматив: пользование контейнером свыше срока — 1000 руб/сутки.</li>
        <li>МКАД: выезд за пределы — 80 руб/км.</li></ul>
        """
        with patch.dict(os.environ, {"TLM_TARIFF_DIR": str(directory)}), \
                patch("backend.network.russia.moscow_today", return_value=date(2027, 1, 15)) as today:
            request = self.send(
                "Новый расчёт. Москва → Новосибирск, 2030 год. Только ж/д. "
                "10 т муки в мешках, 20 м³, партия делимая.",
                "new", {"allowed_modes": ["rail"], "cargoes": [{
                    "name": "Мука", "weight_tonnes": 10, "volume_m3": 20,
                    "packaging": "bags", "splittable": True,
                }]},
            )["answer"]["calculation"]["request"]

            def form():
                response = self.client.post("/network/russia/forecasts", json=request)
                self.assertEqual(response.status_code, 200, response.text)
                return response.json()

            def movement(result):
                row = next(row for row in result["candidates"] if row["id"] == result["recommendation"])
                return next(item for item in row["cost_items"] if item["kind"] == "transport")

            checked = datetime(2027, 1, 15, tzinfo=timezone.utc)
            with patch("backend.update_tlm_tariff.fetch_page", return_value=html):
                path = refresh(directory, now=checked)
                self.assertIsNone(refresh(directory, now=checked))
            self.assertEqual(movement(form())["base_amount_rub"], 44170)

            today.return_value = date(2027, 2, 1)
            updated = form()
            item = movement(updated)
            self.assertEqual(item["base_amount_rub"], 50000)
            self.assertEqual(item["pricing"]["valid_from"], "2027-02-01T00:00:00+03:00")
            self.assertEqual(item["pricing"]["observed_on"], "2027-01-15")
            self.assertIn(item["pricing"]["tariff_version"], path.name)
            self.assertEqual([factor["year"] for factor in item["index_factors"]], [2028, 2029, 2030])
            self.assertEqual(self.send("А если в 2030 году?")["answer"]["calculation"], updated)

            with patch("backend.update_tlm_tariff.fetch_page", return_value=html.replace(rows[0], "")):
                with self.assertRaisesRegex(ValueError, "пропали"):
                    refresh(directory, now=checked)
            self.assertEqual(list(directory.glob("*.json")), [path])
            self.assertEqual(form(), updated)

    def test_air_and_land_share_one_comparison(self):
        dialog = self.send(
            "Как доставить 100 кг муки из Москвы в Новосибирск в 2030 году? "
            "Мешки по 25 кг, партия делимая, объём 0,2 м³. Размер мешка 0,5 × 0,4 × 0,2 м.",
            "new", {"origin": "Москва", "destination": "Новосибирск", "target_year": 2030,
                    "cargoes": [{"name": "Мука", "weight_tonnes": .1}]},
        )
        answer = dialog["answer"]
        self.assertEqual(answer["kind"], "calculation", answer)
        result = answer["calculation"]
        modes = {tuple(row["modes"]) for row in result["candidates"]}
        self.assertTrue({("road",), ("rail",), ("air",)} <= modes)
        self.assertTrue(any(set(chain) == {"road", "rail"} for chain in modes))
        selected = next(row for row in result["candidates"] if row["id"] == result["recommendation"])
        self.assertNotIn("air", selected["modes"])
        self.assertIn("Авиа, базовый сценарий", answer["reply"])
        form = self.client.post("/network/russia/forecasts", json=result["request"])
        self.assertEqual(form.status_code, 200, form.text)
        self.assertEqual(result, form.json())

    def test_partial_quote_clarification_year_and_form_parity(self):
        first = self.send(
            "Сколько стоит перевезти 100 тонн муки из Москвы в Новосибирск в 2030 году?",
            "new", {"target_year": 2030, "cargoes": [{"name": "Мука", "weight_tonnes": 100}],
                    "stops": [{"operation": "add", "city": "Москва"}, {"operation": "add", "city": "Новосибирск"}]},
        )
        self.assertEqual(set(first["answer"]["missing_fields"]), {"cargoes.0.packaging", "cargoes.0.splittable", "cargoes.0.volume_m3"})
        complete = self.send(
            "В мешках по 25 кг, 130 м³, партию можно разделить.", "update",
            {"cargoes": [{"packaging": "bags", "weight_tonnes": 0.025, "volume_m3": 130,
                          "splittable": True, "properties": {"handling_profile": "dry_general"}}]},
            evidence=[{"path": f"cargoes.0.{field}", "quote": quote} for field, quote in (
                ("packaging", "В мешках"), ("weight_tonnes", "25 кг"), ("splittable", "партию можно разделить"),
                ("properties.handling_profile", "В мешках"),
            )],
        )
        self.assertEqual(complete["answer"]["kind"], "calculation")
        result = complete["answer"]["calculation"]
        cargo = result["request"]["cargoes"][0]
        self.assertEqual((cargo["weight_tonnes"], cargo["volume_m3"], cargo["max_piece_tonnes"]), (100, 130, 0.025))
        self.assertIsNone(cargo["properties"])
        self.assertIsNotNone(complete["selected_candidate"])
        calls = self.model.chat.call_count
        changed = self.send("А если в 2028 году?")
        self.assertEqual(self.model.chat.call_count, calls)
        alternate = changed["answer"]["calculation"]
        self.assertEqual(alternate["request"], result["request"] | {"target_year": 2028})
        self.assertNotEqual(alternate["candidates"][0]["total_cost_rub"], result["candidates"][0]["total_cost_rub"])
        self.assertEqual(self.client.post("/network/russia/forecasts", json=alternate["request"]).json(), alternate)

    def test_separate_quotes_catalogue_and_typo(self):
        self.flour()
        for origin, spoken, destination, name, genitive, packaging in (
            ("Екатеринбург", "Екатеренбурга", "Москва", "Офисная бумага", "офисной бумаги", "boxes"),
            ("Санкт-Петербург", "Санкт-Петербурга", "Москва", "Сахар", "сахара", "bags"),
            ("Москва", "Москвы", "Казань", "Рис", "риса", "bags"),
        ):
            pack = "Коробки" if packaging == "boxes" else "Мешки"
            reply = self.send(
                f"Сколко стоит перевезти 100 тонн {genitive} из {spoken} в {destination} в 2030 году? {pack}, 120 м³, делимая партия.",
                "update", {"origin": origin, "destination": destination, "target_year": 2030,
                           "cargoes": [{"name": name, "packaging": packaging, "splittable": True}]},
            )
            result = reply["answer"]["calculation"]
            self.assertEqual((result["request"]["origin"], result["request"]["destination"]), (origin, destination))
            cargo, = result["request"]["cargoes"]
            self.assertEqual((cargo["name"], cargo["weight_tonnes"], cargo["volume_m3"]), (name, 100, 120))
            self.assertIsNone(cargo["max_piece_tonnes"])
            self.assertIsNotNone(result["recommendation"])

    def test_unknown_cargo_requests_properties_and_mass_only_skips_volume(self):
        request = self.send(
            "100 тонн книг из Москвы в Красноярск в 2030 году, делимые коробки.", "new",
            {"origin": "Москва", "destination": "Красноярск", "target_year": 2030,
             "cargoes": [{"name": "Книги", "packaging": "boxes", "splittable": True}]},
        )
        fields = request["answer"]["missing_fields"]
        self.assertIn("cargoes.0.properties.phase", fields)
        self.assertIn("cargoes.0.volume_m3", fields)
        complete = self.send(
            "Сухой твёрдый неопасный груз без особых условий и температурного режима. Посчитай только по массе.", "update",
            {"capacity_basis": "mass_only", "cargoes": [{"properties": {"phase": "solid", "hazardous": False,
                                       "handling_profile": "dry_general", "temperature_control_required": False}}]},
        )
        self.assertEqual(complete["answer"]["kind"], "calculation")
        self.assertEqual(complete["answer"]["calculation"]["request"]["capacity_basis"], "mass_only")

    def test_region_in_new_price_request_does_not_reuse_previous_destination(self):
        self.send("10 т муки из Москвы в Ставрополь в 2030 году, мешки, 20 м³, партия делимая. Только авто.",
                  "new", {"allowed_modes": ["road"]})
        message = ("Сколько будет стоить перевезти 20 тонн муки из Москвы в Якутию в 2031 году? "
                   "Партию можно делить, груз в мешках.")
        for prefix, action in (("", "update"), ("Новый расчёт. ", "clarify")):
            result = self.send(prefix + message, action)
            self.assertEqual(result["answer"]["missing_fields"], ["destination"])
            self.assertIn("Якутск", result["answer"]["reply"])
            self.assertIn("Москва", " ".join(result["known"]))
            self.assertIn("20 т", " ".join(result["known"]))
            self.assertIn("2031", " ".join(result["known"]))
            self.assertNotIn("Ставрополь", " ".join(result["known"]))
        result = self.send("Город назначения Якутск. Общий объём 20 м³.", "update")
        request = result["answer"]["calculation"]["request"]
        self.assertEqual((request["destination"], request["target_year"]), ("Якутск", 2031))
        self.assertIn("air", request["allowed_modes"])

    def test_cargo_clarification_keeps_route_when_model_says_new(self):
        self.flour(destination="Красноярск")
        self.send("Через Екатеринбург и Новосибирск.", "update",
                  {"stops": [{"operation": "add", "city": city} for city in ("Екатеринбург", "Новосибирск")]})
        self.send("А если в 2028 году?")
        pending = self.send("А если повезём карандаши вместо муки?", "update", {"cargoes": [{"name": "Карандаши"}]})
        self.assertIn("cargoes.0.weight_tonnes", pending["answer"]["missing_fields"])
        self.assertFalse(any(".properties." in field for field in pending["answer"]["missing_fields"]))
        completed = self.send(
            "Везём 100 тонн карандашей в коробках, общий объём 120 м³. Партию можно разделить. "
            "Груз сухой, твёрдый и неопасный, без особых условий и температурного режима.",
            "new", {"cargoes": [{"name": "Карандаши"}]},
        )
        self.assertEqual(completed["answer"]["kind"], "calculation")
        request = completed["answer"]["calculation"]["request"]
        self.assertEqual((request["origin"], request["destination"], request["target_year"]), ("Москва", "Красноярск", 2028))
        self.assertEqual([stop["settlement"] for stop in request["stops"]], ["Екатеринбург", "Новосибирск"])
        cargo, = request["cargoes"]
        self.assertEqual((cargo["name"], cargo["weight_tonnes"], cargo["volume_m3"]), ("Карандаши", 100, 120))

    def test_new_pencil_quote_replaces_stale_draft_even_with_invalid_model_reply(self):
        self.send("100 т муки в мешках из Воронежа в Новокузнецк в 2028 году.",
                  "new", evidence=[])
        self.model.chat.return_value = OllamaMessage(content="{bad json")
        pending = self.send("сколько будет стоить перевезти 10 тон карандашей из брянска в оренбург?")
        self.assertIn("Брянск → Оренбург", pending["known"])
        self.assertIn("Карандаши, 10 т", pending["known"])
        self.assertFalse(any("Мука" in line or "2028" in line for line in pending["known"]))
        self.assertFalse(any(".properties." in field for field in pending["answer"]["missing_fields"]))
        model_input = json.loads(self.model.chat.call_args.args[0][-1]["content"])
        self.assertIsNone(model_input["pending"])

        details = self.send("рассчитай стоимость этой перевозки в 2030 году, отправка разовая, "
                            "груз упакован в коробки, объем 20 кубометров", "clarify", evidence=[])
        self.assertEqual(details["answer"]["missing_fields"], ["cargoes.0.splittable"])
        self.model.chat.return_value = OllamaMessage(content="{bad json")
        done = self.send("можно разделить партию")
        self.assertEqual(done["answer"]["kind"], "calculation", done["answer"]["reply"])
        calculation = done["answer"]["calculation"]
        request = calculation["request"]
        self.assertEqual((request["origin"], request["destination"], request["target_year"]),
                         ("Брянск", "Оренбург", 2030))
        cargo, = request["cargoes"]
        self.assertEqual((cargo["name"], cargo["weight_tonnes"], cargo["volume_m3"], cargo["packaging"], cargo["splittable"]),
                         ("Карандаши", 10, 20, "boxes", True))
        self.assertEqual(calculation["road_market_estimate"]["trucks"], 1)
        self.assertEqual(calculation["road_market_estimate"]["forecast_road_rub"], 117620.8)

    def test_full_shipment_ignores_duplicate_cargo_positions_from_model(self):
        message = ("Сколько будет стоить перевезти 100 кг муки в мешках из Брянска в Воронеж в 2030 году? "
                   "Объём 0,25 м³, партию можно разделить. Покажи все варианты")
        self.flour(destination="Казань")
        response = self.send(message, "update", {"cargoes": [
            {"weight_tonnes": .1}, {"volume_m3": .25},
        ], "replace_cargoes": True})
        self.assertEqual(response["answer"]["kind"], "calculation", response["answer"]["reply"])
        request = response["answer"]["calculation"]["request"]
        self.assertEqual((request["origin"], request["destination"], request["target_year"]),
                         ("Брянск", "Воронеж", 2030))
        cargo, = request["cargoes"]
        self.assertEqual((cargo["name"], cargo["weight_tonnes"], cargo["volume_m3"],
                          cargo["packaging"], cargo["splittable"]), ("Мука", .1, .25, "bags", True))
        self.assertTrue({"road", "rail", "air"}.issubset(request["allowed_modes"]))

    def test_duplicate_cargo_with_route_endpoints_counts_once(self):
        message = (
            "Новый расчёт. Сколько стоит перевезти 10 т сахара из Воронежа в Пятигорск в 2027 году? "
            "Разовая отправка, сахар в мешках, объём 9,5 м³, партию можно разделить. "
            "Срок до 3 дней. Сравни авто и ж/д по цене и сроку."
        )
        position = {"name": "Сахар", "weight_tonnes": 10, "volume_m3": 9.5, "packaging": "bags",
                    "pickup_at": "Воронеж", "delivery_at": "Пятигорск"}
        answer = self.send(message, "new", {"cargoes": [position, position]})["answer"]
        self.assertEqual(answer["kind"], "calculation", answer["reply"])
        cargo, = answer["calculation"]["request"]["cargoes"]
        self.assertEqual(cargo["weight_tonnes"], 10)
        self.assertTrue(cargo["splittable"])

    def test_books_request_with_route_preposition_typo_keeps_cargo(self):
        self.flour(destination="Брянск")
        response = self.send("Новый расчет, перевезти из Уфы ы казань 100 тонн книг в 2030 как дешевле",
                             "clarify", {"target_year": 2030})
        self.assertEqual(set(response["answer"]["missing_fields"]),
                         {"cargoes.0.packaging", "cargoes.0.splittable", "cargoes.0.volume_m3"})
        self.assertIn("Уфа → Казань", response["known"])
        self.assertIn("Книги, 100 т", response["known"])
        response = self.send("В коробках, 120 м³, можно разделить партию.", "clarify", evidence=[])
        self.assertEqual(response["answer"]["kind"], "calculation", response["answer"]["reply"])
        request = response["answer"]["calculation"]["request"]
        self.assertEqual((request["origin"], request["destination"], request["target_year"]), ("Уфа", "Казань", 2030))
        cargo, = request["cargoes"]
        self.assertEqual((cargo["name"], cargo["weight_tonnes"], cargo["volume_m3"]), ("Книги", 100, 120))

    def test_full_shipment_is_recovered_when_model_omits_cargo(self):
        for message, name, weight, volume in (
            ("Новый арсчет. Москва → Екатеринбург → Новосибирск → Красноярск, 2028 год. "
             "Везём 100 тонн карандашей в коробках, общий объём 120 м³. Партию можно разделить. "
             "Груз сухой, твёрдый и неопасный, без особых условий и температурного режима.",
             "карандаш", 100, 120),
            ("Новый расчёт: Москва → Екатеринбург → Новосибирск → Красноярск, 2028 год. "
             "Перевезти 10 т муки в мешках, 20 м³, партия делимая.", "мук", 10, 20),
        ):
            with self.subTest(message=message):
                response = self.send(message, "clarify", evidence=[])
                self.assertEqual(response["answer"]["kind"], "calculation", response["answer"]["reply"])
                request = response["answer"]["calculation"]["request"]
                self.assertEqual((request["origin"], request["destination"], request["target_year"]),
                                 ("Москва", "Красноярск", 2028))
                self.assertEqual([stop["settlement"] for stop in request["stops"]], ["Екатеринбург", "Новосибирск"])
                cargo, = request["cargoes"]
                self.assertTrue(cargo["name"].lower().startswith(name))
                self.assertEqual((cargo["weight_tonnes"], cargo["volume_m3"], cargo["splittable"]), (weight, volume, True))

    def test_new_shipment_with_typo_and_repeated_route_keeps_year_and_stop(self):
        self.flour(destination="Брянск")
        self.send("Добавь остановку в Калуге.", "update",
                  {"stops": [{"operation": "add", "city": "Калуга"}]})
        self.send("Повезём книги вместо муки.", "update", {"cargoes": [{"name": "Книги"}]})
        message = ("Сколько стоит перевезти 10 тонн муки из Москвы в снкт петербург в 2032 году? "
                   "С остановкой в Брянске на 2 часа.")
        for prefix, action in (("", "update"), ("Новый расчёт. ", "clarify")):
            with self.subTest(prefix=prefix):
                first = self.send(prefix + message, action, evidence=[])
                self.assertEqual(set(first["answer"]["missing_fields"]), {
                    "cargoes.0.packaging", "cargoes.0.splittable", "cargoes.0.volume_m3",
                })
                self.assertIn("Москва → Санкт-Петербург", first["known"])
                self.assertIn("Через Брянск (+2 ч)", first["known"])
                self.assertIn("2032 год", first["known"])
                self.assertFalse(any("Калуга" in line or "Книги" in line for line in first["known"]))

        complete = self.send(
            "Из Москвы в Санкт Петербург, 10 тонн муки везём, груз в мешках, "
            "партию можно разделить, объём 20 м³.",
            "new", evidence=[],
        )
        self.assertEqual(complete["answer"]["kind"], "calculation", complete["answer"]["reply"])
        result = complete["answer"]["calculation"]
        request = result["request"]
        self.assertEqual((request["origin"], request["destination"], request["target_year"]),
                         ("Москва", "Санкт-Петербург", 2032))
        self.assertEqual([(stop["settlement"], stop["dwell_hours"]) for stop in request["stops"]], [("Брянск", 2)])
        network = load_russia()
        form_request = request | {
            "origin": network.resolve(request["origin"]),
            "destination": network.resolve(request["destination"]),
            "stops": [stop | {"settlement": network.resolve(stop["settlement"])} for stop in request["stops"]],
        }
        form = self.client.post("/network/russia/forecasts", json=form_request)
        self.assertEqual(form.status_code, 200, form.text)
        self.assertEqual(form.json(), result)

    def test_stop_anchor_answer_can_also_correct_destination(self):
        self.flour(destination="Брянск")
        self.send("Добавь остановку в Калуге.", "update",
                  {"stops": [{"operation": "add", "city": "Калуга"}]})
        pending = self.send("Добавь остановку в Брянске на 2 часа.", "clarify", evidence=[])
        self.assertIn("После какого города", pending["answer"]["reply"])
        reply = self.send("После Москвы, конечная точка маршрута Санкт Петербург.", "clarify", evidence=[])
        self.assertEqual(reply["answer"]["kind"], "calculation", reply["answer"]["reply"])
        request = reply["answer"]["calculation"]["request"]
        self.assertEqual(request["destination"], "Санкт-Петербург")
        self.assertEqual([(s["settlement"], s["dwell_hours"]) for s in request["stops"]],
                         [("Брянск", 2), ("Калуга", 0)])

    def test_dated_estimate_uses_form_defaults_and_horizon(self):
        reply = self.send(
            "Офисная бумага, 8 т, 12 м³, делимые коробки, из Москвы в Санкт-Петербург. Груз готов 20 ноября 2026 года.",
            "new", {"origin": "Москва", "destination": "Санкт-Петербург",
                    "cargoes": [{"name": "Офисная бумага", "packaging": "boxes", "splittable": True, "volume_m3": 12}]},
        )
        self.assertEqual(reply["answer"]["missing_fields"], ["schedule.ready_at"])
        self.assertIn("Дата готовности — 2026-11-20", reply["known"])
        reply = self.send("В 10:00 МСК")
        result = reply["answer"]["calculation"]
        self.assertEqual(reply["answer"]["tool"], "calculate_russia")
        self.assertIsNone(result["request"]["max_delivery_hours"])
        form = self.client.post("/calculations", json=result["request"] | {"max_delivery_hours": 90 * 24})
        self.assertEqual(form.status_code, 200, form.text)
        self.assertEqual(form.json() | {"request": result["request"]}, result)
        ambiguous = self.send("А если в 2028 году?")
        self.assertEqual(ambiguous["answer"]["missing_fields"], ["schedule.ready_at"])
        year_only = self.send("Без даты.")
        self.assertEqual(year_only["answer"]["calculation"]["forecast"]["target_year"], 2028)

    def test_visit_only_stop_with_city_typo_preserves_current_shipment(self):
        original = self.send(
            "100 т муки из Москвы в Красноярск в 2032 году, делимые мешки, 20 м³.",
            "new", {"origin": "Москва", "destination": "Красноярск", "target_year": 2032,
                    "cargoes": [{"name": "Мука", "weight_tonnes": 100, "volume_m3": 20,
                                 "splittable": True, "packaging": "bags"}]},
        )["answer"]["calculation"]["request"]
        calls = self.model.chat.call_count
        updated = self.send("а если с остановкой в екатеренбурге без погрузки и выгрузки")
        self.assertEqual(self.model.chat.call_count, calls)
        request = updated["answer"]["calculation"]["request"]
        stop, = request["stops"]
        self.assertEqual(stop["settlement"], "Екатеринбург")
        self.assertEqual(stop["action"], "visit")
        self.assertEqual(stop["pickup_cargo_ids"], [])
        self.assertEqual(stop["delivery_cargo_ids"], [])
        self.assertEqual(request, original | {"stops": [stop]})

    def test_free_form_stop_even_when_model_asks_to_clarify(self):
        for message, city in (
            ("а остановку через брянск добавь", "Брянск"),
            ("по пути заедем в Казань", "Казань"),
        ):
            with self.subTest(message=message):
                self.flour()
                answer = self.send(message, "clarify")["answer"]
                self.assertEqual(answer["kind"], "calculation")
                stop, = answer["calculation"]["request"]["stops"]
                self.assertEqual((stop["settlement"], stop["action"]), (city, "visit"))

    def test_stop_survives_year_change_and_comparison(self):
        self.flour()
        stopped = self.send("Через Владимир, остановка на 2 часа.", "update",
                            {"stops": [{"operation": "add", "city": "Владимир", "dwell_hours": 2}]})
        self.assertEqual(stopped["answer"]["kind"], "calculation")
        original = stopped["answer"]["calculation"]
        stop, = original["request"]["stops"]
        self.assertEqual((stop["settlement"], stop["action"], stop["dwell_hours"]), ("Владимир", "visit", 2))
        self.assertIn("Владимир", stopped["settlement_names"].values())
        updated = self.send("А если в 2028 году?")
        self.assertEqual(updated["answer"]["calculation"]["request"]["stops"], [stop])
        compared = self.send("Сравни 2028 и 2030 годы")
        self.assertIn("2028 год", compared["known"])
        for year, row in zip((2028, 2030), compared["scenarios"]):
            result = row["answer"]["calculation"]
            self.assertEqual(result["forecast"]["target_year"], year)
            self.assertEqual(result["request"]["stops"], [stop])
        form_request = original["request"] | {"stops": [stop | {"settlement": load_russia().resolve("Владимир")}]}
        self.assertEqual(self.client.post("/network/russia/forecasts", json=form_request).json(), original)

    def test_stop_edits_keep_order_and_prompt_for_missing_anchor(self):
        self.flour(destination="Уфа")
        for message, edits, expected in (
            ("Через Владимир и Казань.", [{"operation": "add", "city": "Владимир"}, {"operation": "add", "city": "Казань"}], "Владимир → Казань"),
            ("Вместо Казани через Нижний Новгород.", [{"operation": "replace", "city": "Казань", "replacement": "Нижний Новгород"}], "Владимир → Нижний Новгород"),
            ("Переставь Владимир после Нижнего Новгорода.", [{"operation": "move", "city": "Владимир", "after": "Нижний Новгород"}], "Нижний Новгород → Владимир"),
            ("Убери Владимир.", [{"operation": "remove", "city": "Владимир"}], "Нижний Новгород"),
        ):
            result = self.send(message, "update", {"stops": edits})
            self.assertIn("Через " + expected, result["known"])
        answer = self.send("Добавь Казань.", "update", {"stops": [{"operation": "add", "city": "Казань"}]})
        self.assertEqual(answer["answer"]["missing_fields"], ["stops"])
        self.assertIn("Через Нижний Новгород", answer["known"])
        placed = self.send("После Нижнего Новгорода.")
        self.assertIn("Через Нижний Новгород → Казань", placed["known"])

    def test_stop_delivery_positions_and_missing_volume(self):
        shipment = {"origin": "Москва", "destination": "Уфа", "target_year": 2030,
                    "stops": [{"operation": "add", "city": "Казань", "action": "delivery"}],
                    "cargoes": [
                        {"name": "Мука", "weight_tonnes": 4, "packaging": "bags", "splittable": True, "delivery_at": "Казань"},
                        {"name": "Мука", "weight_tonnes": 6, "volume_m3": 12, "packaging": "bags", "splittable": True},
                    ]}
        first = self.send(
            "Москва → Казань → Уфа, в Казани выгрузить 4 т муки, в Уфе — 6 т (12 м³), делимые мешки, 2030 год.",
            "new", shipment,
        )
        self.assertEqual(first["answer"]["missing_fields"], ["cargoes.0.volume_m3"])
        complete = self.send("Для Казани 8 м³.", "update", {"cargoes": [{"id": "cargo-1", "volume_m3": 8}]})
        result = complete["answer"]["calculation"]
        self.assertEqual([cargo["volume_m3"] for cargo in result["request"]["cargoes"]], [8, 12])
        self.assertEqual(result["request"]["stops"][0]["delivery_cargo_ids"], ["cargo-1"])
        asked = self.send("Убери остановку в Казани.", "update", {"stops": [{"operation": "remove", "city": "Казань"}]})
        self.assertEqual(asked["answer"]["missing_fields"], ["cargoes.0.delivery_at"])
        removed = self.send("Выгрузи эту позицию в Уфе.", "update",
                            {"cargoes": [{"id": "cargo-1", "delivery_at": "Уфа"}]})
        self.assertEqual(removed["answer"]["calculation"]["request"]["stops"], [])

    def test_failure_replay_revision_and_unsupported(self):
        first = self.flour()
        self.model.chat.return_value = OllamaMessage(content="{bad json")
        invalid = self.send("Поменяй объём")
        self.assertEqual(invalid["answer"]["kind"], "clarification")
        self.assertEqual(invalid["known"], first["known"])
        failed_id = uuid4()
        self.model.chat.side_effect = AssistantUnavailable
        self.send("Объём 25 м³", turn_id=failed_id, status=503)
        self.assertEqual(self.client.get("/assistant/dialog").json()["revision"], invalid["revision"])
        updated = self.send("Объём 25 м³", "update", {"cargoes": [{"volume_m3": 25}]}, turn_id=failed_id)
        self.assertEqual(self.send("Объём 25 м³", turn_id=failed_id, revision=invalid["revision"]), updated)
        self.assertEqual(self.send("Иное сообщение", revision=invalid["revision"], status=409)["error"]["code"], "assistant_revision_conflict")
        self.assertEqual(self.send("Иное сообщение", turn_id=failed_id, status=409)["error"]["code"], "assistant_turn_reused")
        unsupported = self.send("Можно забронировать перевозку?", "unsupported")
        self.assertIsNone(unsupported["answer"]["calculation"])
        self.assertEqual(unsupported["known"], updated["known"])

    def test_storage_accepts_previous_format_and_rejects_concurrent_turn(self):
        saved = self.flour()
        db = self.app.database
        with db.connect() as connection:
            row = connection.execute("SELECT state FROM assistant_dialogs WHERE user_id = ?", ("chat-test",)).fetchone()
            state = json.loads(row["state"])
            state["lookup"] = {"tool": "lookup_connectivity", "arguments": {}}
            state["comparison"] = [{"target_year": 2028}]
            state["current"]["options"]["budget_rub"] = 100000
            connection.execute("UPDATE assistant_dialogs SET state = ? WHERE user_id = ?", (json.dumps(state), "chat-test"))
        self.assertEqual(self.client.get("/assistant/dialog").json()["known"], saved["known"])
        request = DialogRequest(message="Проверка", turn_id=uuid4(), revision=self.revision)
        with self.assertRaisesRegex(RuntimeError, "cancel"):
            with dialog_turn(db, "chat-test", request):
                blocked = self.send("Другое сообщение", status=409)
                self.assertEqual(blocked["error"]["code"], "assistant_in_progress")
                raise RuntimeError("cancel")

    def test_guest_dialog_keeps_conditions_and_isolated_from_other_sessions(self):
        self.flour(destination="Якутск")
        self.client.cookies.clear()
        self.send("Привет", revision=0, status=409)
        guest = self.client.get("/assistant/dialog")
        self.assertEqual(guest.status_code, 200)
        self.assertEqual(guest.json()["revision"], 0)
        self.assertIn("HttpOnly", guest.headers["Set-Cookie"])
        self.revision = 0
        first = self.flour()
        self.assertEqual(first["answer"]["kind"], "calculation")
        updated = self.send("А если в 2028 году?")
        self.assertEqual(updated["answer"]["calculation"]["request"]["target_year"], 2028)
        self.assertEqual(self.client.get("/assistant/dialog").json()["known"], updated["known"])
        other = TestClient(self.client.app)
        self.addCleanup(other.close)
        self.assertEqual(other.get("/assistant/dialog").json()["known"], [])
