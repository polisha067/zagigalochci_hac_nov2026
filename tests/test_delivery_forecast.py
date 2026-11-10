import json
import unittest
from copy import deepcopy
from decimal import Decimal

from backend.network.delivery_forecast import ForecastRequest, calculate_forecast
from backend.network.forecast_indices import ForecastIndices, SNAPSHOT_PATH, load_indices
from backend.network.russia import load_russia
from tests import BACKEND_DIR, test_assistant


def shipment(**changes):
    example = BACKEND_DIR / "examples/russia-request.json"
    return dict(json.loads(example.read_text(encoding="utf-8")),
                **({"schedule": None, "max_delivery_hours": None, "target_year": 2030} | changes))


def recommended(result):
    return next(row for row in result.candidates if row["id"] == result.recommendation)


class ForecastModelTests(unittest.TestCase):
    def calculate(self, indices=None, **changes):
        return calculate_forecast(ForecastRequest.model_validate(shipment(**changes)), indices=indices)

    def test_real_scenarios_use_the_same_base_with_distinct_year_coefficients(self):
        first = self.calculate(destination="Владивосток", target_year=2028)
        second = self.calculate(destination="Владивосток")
        a, b = recommended(first), recommended(second)
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(a["forecast_price"]["base_rub"], b["forecast_price"]["base_rub"])
        self.assertGreater(b["total_cost_rub"], a["total_cost_rub"])
        self.assertTrue(any(row["modes"] == ["road"] for row in second.candidates))
        self.assertTrue(any(row["multimodal"] and row["complete"] for row in second.candidates))
        self.assertEqual(b["modes"], ["rail"])
        movement = next(item for item in b["cost_items"] if item["kind"] == "transport")
        self.assertEqual(movement["base_amount_rub"], 109850)
        self.assertEqual(movement["pricing"]["source_ids"], ["tlm-rail-2026"])
        self.assertFalse(any(item["kind"] in ("pickup_handling", "delivery_handling") for item in b["cost_items"]))
        self.assertEqual(b["total_cost_rub"], 184508.96)
        self.assertEqual(b["max_delivery_hours"], 301.5)
        self.assertFalse(second.forecast["alternative"]["ranges_overlap"])
        self.assertIsNone(second.request["schedule"])
        self.assertIsNone(second.request["max_delivery_hours"])
        self.assertEqual(b["total_cost_rub"], float(sum(Decimal(str(item["amount_rub"])) for item in b["cost_items"])))
        self.assertTrue(all("at" not in item for item in b["cost_items"]))
        self.assertTrue(all("departure_at" not in step for step in b["steps"]))
        self.assertTrue(all(item["pricing"]["source_ids"] for item in b["cost_items"]))
        self.assertTrue(all([p["year"] for p in item["index_factors"]] == [2027, 2028, 2029, 2030]
                            for item in b["cost_items"]))
        self.assertTrue(b["forecast_price"]["lower_rub"] < b["total_cost_rub"] < b["forecast_price"]["upper_rub"])
        self.assertTrue(load_indices().series["rail"].backtest(5))

    def test_finds_a_published_coefficient_and_ranks_before_limiting_output(self):
        raw = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        for category, factor in (("road", 100), ("rail", 400)):
            raw["series"][category]["points"].append([2027, factor, "delivery-index-model-v1", "published"])
        result = self.calculate(ForecastIndices(raw), target_year=2027, max_candidates=1)
        self.assertEqual(len(result.candidates), 1)
        self.assertGreater(result.search["evaluated_candidates"], 1)
        chosen = recommended(result)
        self.assertEqual(set(chosen["modes"]), {"road"})
        movement = next(item for item in chosen["cost_items"] if item["kind"] == "transport")
        self.assertEqual(movement["base_amount_rub"], movement["amount_rub"])
        self.assertEqual(movement["index_factors"][0]["kind"], "published")

    def test_missing_index_is_partial_and_a_limit_can_rule_out_all_routes(self):
        raw = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        raw["proxies"].pop("handling")
        result = self.calculate(ForecastIndices(raw))
        self.assertIsNone(result.recommendation)
        row = next(row for row in result.candidates if row["modes"] == ["road"])
        self.assertIsNone(row["total_cost_rub"])
        self.assertGreater(row["forecast_price"]["known_subtotal_rub"], 0)
        self.assertIn("missing_forecast_index", {issue["code"] for issue in row["issues"]})
        self.assertIsNone(self.calculate(max_delivery_hours=1).recommendation)

    def test_river_snapshot_dates_are_preserved(self):
        payload = shipment(origin="Красноярск", destination="Дудинка",
                           schedule={"mode": "once", "ready_at": "2026-06-08T08:00:00+07:00"},
                           allowed_modes=["river"], max_delivery_hours=480)
        original = deepcopy(payload)
        original.pop("target_year")
        network = load_russia()
        self.assertTrue(network.calculate(original)["recommendation"])
        payload["schedule"]["ready_at"] = "2030-06-08T08:00:00+07:00"
        future = calculate_forecast(ForecastRequest.model_validate(payload))
        self.assertIsNone(future.recommendation)
        self.assertTrue(future.candidates)
        self.assertTrue(all(row["total_cost_rub"] is None for row in future.candidates))
        self.assertTrue(any(issue["code"] == "expired_evidence" for row in future.candidates for issue in row["issues"]))
        self.assertTrue(network.calculate(original)["recommendation"])

    def test_long_term_scenario_does_not_publish_an_uncalibrated_range(self):
        result = self.calculate(target_year=2035)
        budget = recommended(result)["forecast_price"]
        self.assertTrue(budget["long_term"])
        self.assertIsNone(budget["lower_rub"])
        self.assertEqual(budget["range_status"], "not_calibrated")


class ForecastApiTests(unittest.TestCase):
    def setUp(self):
        self.case = test_assistant.AssistantApiTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.client = self.case.client

    def test_chat_clarifies_and_then_matches_guest_form_calculation(self):
        payload = shipment(destination="Владивосток")
        cargo = payload["cargoes"][0]
        tool = "estimate_future_delivery_cost"
        self.case.connect_ollama(
            self.case.domain_reply(tool, origin="Москва", destination="Владивосток", target_year=2030,
                                   allowed_modes=["road", "rail"], cargoes=[{k: cargo[k] for k in ("id", "name", "weight_tonnes")}]),
            self.case.domain_reply(tool, cargoes=[cargo], continue_previous=True),
        )
        clarification = self.client.post("/assistant/chat", json={
            "message": "Перевезти 10 т муки из Москвы во Владивосток в 2030 году. Что дешевле?"
        })
        self.assertEqual(clarification.status_code, 200, clarification.text)
        first = clarification.json()
        self.assertEqual(first["tool"], tool)
        self.assertEqual(first["kind"], "clarification")
        self.assertEqual(set(first["missing_fields"]), {"cargoes.0.packaging", "cargoes.0.splittable", "cargoes.0.volume_m3"})
        followup = self.client.post("/assistant/chat", json={
            "message": "Мука в мешках, объём 20 м³, делимая. До 25 кг и 0,5 × 0,4 × 0,2 м на мешок.",
            "pending": first["pending"],
        })
        self.assertEqual(followup.status_code, 200, followup.text)
        chat = followup.json()
        self.assertEqual(chat["kind"], "calculation")
        self.assertIn("184 500 ₽", chat["reply"])
        self.assertIn("от станции до станции", chat["reply"])
        self.assertTrue(any(source["url"] and "rosstat.gov.ru" in source["url"] for source in chat["sources"]))
        self.client.cookies.clear()
        network = load_russia()
        payload["origin"] = network.resolve(payload["origin"])
        payload["destination"] = network.resolve(payload["destination"])
        form = self.client.post("/network/russia/forecasts", json=payload)
        self.assertEqual(form.status_code, 200, form.text)
        self.assertEqual(chat["calculation"], form.json())

    def test_cross_year_schedule_is_rejected(self):
        response = self.client.post("/network/russia/forecasts", json=shipment(schedule={
            "mode": "explicit", "ready_at": ["2029-12-31T08:00:00+03:00", "2030-01-01T08:00:00+03:00"],
        }))
        self.assertEqual(response.status_code, 422)

    def test_form_options_are_distinct_from_the_a1_volume_forecast(self):
        prices = self.client.get("/network/russia/options?limit=1").json()["delivery_forecast"]
        volume = self.client.get("/network/forecast/options").json()
        self.assertEqual(prices["min_year"], 2027)
        self.assertEqual(prices["max_year"], 2035)
        self.assertEqual(volume["max_year"], 2100)

    def test_regular_chat_forecast_requests_missing_period(self):
        cargo = shipment()["cargoes"][0]
        tool = "estimate_future_delivery_cost"
        self.case.connect_ollama(self.case.domain_reply(
            tool, origin="Москва", destination="Брянск", target_year=2030, cargoes=[cargo],
        ))
        response = self.client.post("/assistant/chat", json={
            "message": "Сколько будет стоить регулярно перевозить муку из Москвы в Брянск в 2030 году?",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["kind"], "clarification")
        self.assertEqual(set(response.json()["missing_fields"]),
                         {"schedule.start_at", "schedule.end_at", "schedule.frequency"})
