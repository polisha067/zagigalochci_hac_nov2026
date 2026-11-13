import json
import unittest
from copy import deepcopy
from decimal import Decimal
from backend.app.assistant.results import engine_summary

from backend.network.delivery_forecast import (ForecastRequest, calculate_forecast,
    SavedPriceSeriesRequest, saved_price_series)
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
    def test_reefer_market_preserves_cold_chain_across_stops(self):
        for origin, stop, destination, bounds in (
            ("Ижевск", None, "Иваново", [-20, -18]),
            ("Москва", "Екатеринбург", "Красноярск", [-20, -18]),
            ("Новосибирск", "Красноярск", "Иркутск", [2, 6]),
            ("Воронеж", "Ростов-на-Дону", "Краснодар", [2, 6]),
        ):
            with self.subTest(origin=origin, destination=destination):
                stops = [{"settlement": stop, "action": "visit", "dwell_hours": 2}] if stop else []
                payload = shipment(origin=origin, destination=destination, stops=stops,
                                   allowed_modes=["road", "rail", "air"])
                payload["cargoes"][0].update(name="Рыба", cargo_type=None, weight_tonnes=25, volume_m3=35,
                                            packaging="boxes", splittable=True, temperature_c=bounds,
                                            max_piece_tonnes=None, max_piece_dimensions_m=None)
                result = calculate_forecast(ForecastRequest.model_validate(payload))
                route = result.land_market["routes"][0]
                self.assertEqual(route["transfer_count"], 0)
                self.assertEqual([(leg["equipment"], leg["units"]) for leg in route["legs"]],
                                 [("Рефрижератор 20 т", 2)] * (len(stops) + 1))
                self.assertGreater(route["movement_rub"], 0)
                self.assertIsNone(result.published_reference)
                self.assertTrue(all(row["complete"] for row in result.candidates))
        direct = payload | {"origin": "Екатеринбург", "destination": "Красноярск", "stops": []}
        published = calculate_forecast(ForecastRequest.model_validate(direct))
        part = published.land_market["routes"][0]["legs"][0]
        self.assertEqual((part["kind"], part["base_unit_rub"], part["vat"]),
                         ("published_base", 237600, "included"))
        payload["cargoes"][0]["temperature_c"] = [-30, -25]
        self.assertIsNone(calculate_forecast(ForecastRequest.model_validate(payload)).land_market)
        payload["cargoes"][0].pop("temperature_c")
        result = calculate_forecast(ForecastRequest.model_validate(payload))
        self.assertTrue(any(row["code"] == "missing_temperature_range" for row in result.clarifications))

    def test_regional_air_market_prices_both_legs_at_the_requested_stop(self):
        payload = shipment(origin="Новосибирск", destination="Иркутск", allowed_modes=["air"],
                           stops=[{"settlement": "Красноярск", "action": "visit", "dwell_hours": 2}])
        payload["cargoes"][0].update(name="Мука", weight_tonnes=10, volume_m3=20,
                                    packaging="bags", splittable=True, max_piece_tonnes=None,
                                    max_piece_dimensions_m=None)
        result = calculate_forecast(ForecastRequest.model_validate(payload))
        option = next(row for row in result.air_market["routes"]
                      if [leg["published_rub_per_kg"] for leg in row["legs"]] == [229, 111])
        self.assertEqual([(leg["departure"], leg["arrival"]) for leg in option["legs"]],
                         [("OVB", "KJA"), ("KJA", "IKT")])
        self.assertTrue(all(leg["shipment_count"] == 21 for leg in option["legs"]))
        self.assertAlmostEqual(option["forecast_rub"], sum(leg["forecast_rub"] for leg in option["legs"]))

    def test_road_market_estimates_izhevsk_ivanovo_without_truck_router(self):
        from unittest.mock import patch

        payload = shipment(origin="Ижевск", destination="Иваново", allowed_modes=["road", "rail", "air"])
        payload["cargoes"][0].update(name="Книги", cargo_type=None, weight_tonnes=100,
                                    volume_m3=20, packaging="boxes", splittable=True,
                                    max_piece_tonnes=None, max_piece_dimensions_m=None)
        with patch("backend.network.land_market.truck_path", return_value={"status": "router_unavailable"}):
            result = calculate_forecast(ForecastRequest.model_validate(payload))
        route = result.land_market["routes"][0]
        leg = route["legs"][0]
        self.assertEqual((leg["kind"], leg["distance_source"], leg["units"]),
                         ("model", "regional_carrier_reference", 5))
        self.assertGreater(route["movement_rub"], 0)
        self.assertIn("Иваново", engine_summary(result))

    def test_regional_bulk_road_and_air_use_independent_rates(self):
        for origin, city, expected in (
            ("Москва", "Владивосток", {"road": 4_751_250, "air": 20_800_000}),
            ("Чебоксары", "Красноярск", {"road": 2_801_250, "air": 18_900_000}),
            ("Москва", "Магадан", {"air": 41_200_000}),
        ):
            with self.subTest(origin=origin, city=city):
                payload = shipment(origin=origin, destination=city, allowed_modes=["road", "rail", "air"])
                payload["cargoes"][0].update(name="Книги", cargo_type=None, weight_tonnes=100,
                                            volume_m3=20, packaging="boxes", splittable=True,
                                            max_piece_tonnes=None, max_piece_dimensions_m=None)
                result = calculate_forecast(ForecastRequest.model_validate(payload))
                routes = [row for row in result.land_market["routes"] if row.get("scenario_note")]
                self.assertEqual({row["legs"][-1]["mode"]: row["legs"][-1]["base_unit_rub"]
                                  for row in routes}, expected)
                self.assertTrue(all(len(row["legs"]) == (1 if origin == "Москва" else 3) for row in routes))

    def test_yakutsk_bulk_budget_uses_separate_price_components(self):
        payload = shipment(origin="Чебоксары", destination="Якутск", allowed_modes=["road", "rail", "air"])
        payload["cargoes"][0].update(name="Книги", cargo_type=None, weight_tonnes=100, volume_m3=20,
                                    packaging="boxes", splittable=True, max_piece_tonnes=None,
                                    max_piece_dimensions_m=None)
        request = ForecastRequest.model_validate(payload)
        result = calculate_forecast(request)
        routes = [row for row in result.land_market["routes"] if row.get("scenario_note")]
        self.assertEqual(len(routes), 4)
        cheapest = routes[0]
        self.assertEqual([leg["units"] for leg in cheapest["legs"]], [5, 5, 6, 6])
        self.assertEqual(cheapest["legs"][-1]["origin"], "Нижний Бестях")
        self.assertEqual(cheapest["vat"], "mixed")
        self.assertAlmostEqual(cheapest["movement_rub"], sum(leg["forecast_rub"] for leg in cheapest["legs"]))
        self.assertEqual(routes[-1]["legs"][-1]["base_unit_rub"], 47_000_000)
        reply = engine_summary(result)
        self.assertIn("Нижний Бестях", reply)
        self.assertIn("авиа", reply)
        self.assertNotIn("не удалось рассчитать полную стоимость", reply)
        verified = calculate_forecast(request.model_copy(update={"data_policy": "verified_only"}))
        self.assertIsNone(verified.land_market)

    def calculate(self, indices=None, **changes):
        return calculate_forecast(ForecastRequest.model_validate(shipment(**changes)), indices=indices)

    def test_published_rail_prices_apply_on_both_sides_of_a_stop(self):
        result = self.calculate(destination="Красноярск", allowed_modes=["rail"], stops=[
            {"settlement": "Новосибирск", "action": "visit", "dwell_hours": 2},
        ])
        candidate = next(row for row in result.candidates if row["modes"] == ["rail", "rail"] and row["complete"])
        movement = [item for item in candidate["cost_items"] if item["kind"] == "transport"]
        self.assertEqual([(item["pricing"]["source_ids"], item["base_amount_rub"]) for item in movement],
                         [(["tlm-rail-2026"], 44170), (["sibir-container-rail-2026"], 75000)])
        self.assertEqual(candidate["total_cost_rub"], 210734.41)

    def test_annual_prices_include_intermediate_years_and_match_totals(self):
        result = self.calculate(allowed_modes=["road"], target_year=2030)
        row = recommended(result)
        budget = row["forecast_price"]
        series = budget["annual_series"]
        self.assertEqual([point["year"] for point in series], list(range(budget["base_year"], 2031)))
        self.assertEqual(series[0]["amount_rub"], budget["base_rub"])
        self.assertEqual(series[-1]["amount_rub"], row["total_cost_rub"])
        self.assertEqual(series[-1]["lower_rub"], budget["lower_rub"])
        indices = load_indices()
        for point in series:
            expected = sum(
                (Decimal(str(item["base_amount_rub"])) * indices.estimate(
                    item["pricing"]["category"], item["pricing"]["effective_price_year"],
                    point["year"]).central).quantize(Decimal(".01"))
                for item in row["cost_items"]
            )
            self.assertAlmostEqual(point["amount_rub"], float(expected), places=2)

    def test_archived_ledger_restores_same_annual_series_for_road_and_rail(self):
        for modes in (["road"], ["rail"]):
            with self.subTest(modes=modes):
                result = self.calculate(allowed_modes=modes, target_year=2030)
                row = recommended(result)
                budget = row["forecast_price"]
                payload = dict(base_year=budget["base_year"], target_year=2030,
                               base_rub=budget["base_rub"], total_rub=row["total_cost_rub"],
                               indices_version=result.forecast["indices_version"],
                               model_version=result.forecast["model_version"],
                               charges=[dict(category=item["pricing"]["category"],
                                             effective_price_year=item["pricing"]["effective_price_year"],
                                             base_amount_rub=item["base_amount_rub"])
                                        for item in row["cost_items"]])
                restored = saved_price_series(SavedPriceSeriesRequest.model_validate(payload))
                self.assertEqual(restored["annual_series"], budget["annual_series"])
                for change in ({"total_rub": payload["total_rub"] + 1},
                               {"indices_version": "old-version"},
                               {"model_version": "old-model"}):
                    with self.assertRaises(ValueError):
                        saved_price_series(SavedPriceSeriesRequest.model_validate(payload | change))

    def test_road_stop_uses_published_rates_on_both_legs(self):
        result = self.calculate(destination="Красноярск", allowed_modes=["road"], stops=[
            {"settlement": "Екатеринбург", "action": "visit", "dwell_hours": 2},
        ])
        row = next(row for row in result.candidates if row["modes"] == ["road", "road"] and row["complete"])
        movement = [item for item in row["cost_items"] if item["kind"] == "transport"]
        self.assertEqual([(item["pricing"]["source_ids"], item["base_amount_rub"]) for item in movement],
                         [(["tlm-road-2026"], 200000), (["logistic-avto-road-2026"], 115787)])
        self.assertEqual(row["total_cost_rub"], 513358.79)
        self.assertTrue(any(step.get("dwell_hours") == 2 for step in row["steps"]))

    def test_air_tariff_forecast_is_partial_and_respects_the_horizon(self):
        payload = shipment(destination="Якутск", allowed_modes=["air"], max_legs=1)
        payload["cargoes"][0].update(weight_tonnes=.1, volume_m3=.2)
        for year, median, softened in ((2027, 29617.50, None), (2028, 31898.05, 30736.63),
                                       (2030, 36999.47, 31603.64)):
            with self.subTest(year=year):
                result = self.calculate(**(payload | {"target_year": year}))
                row, = result.candidates
                self.assertIsNone(result.recommendation)
                self.assertIsNone(row["total_cost_rub"])
                self.assertEqual(row["forecast_price"]["air_base_rub"], 27500)
                self.assertEqual(row["forecast_price"]["air_median_rub"], median)
                self.assertEqual(row["forecast_price"]["air_softened_rub"], softened)
                self.assertEqual([(item["kind"], item["amount_rub"]) for item in row["cost_items"]],
                                 [("transport", median)])
                self.assertIn("yakutia-air-cargo-q4-2026", row["source_ids"])
                self.assertTrue(all("departure_at" not in step for step in row["steps"]))
        dated = self.calculate(**(payload | {"target_year": 2027, "schedule": {
            "mode": "once", "ready_at": "2027-04-01T08:00:00+03:00",
        }}))
        self.assertAlmostEqual(dated.candidates[0]["forecast_price"]["air_median_rub"], 28539, delta=15)
        self.assertFalse(self.calculate(**(payload | {"destination": "Брянск"})).candidates)
        payload["cargoes"][0].pop("max_piece_dimensions_m")
        incomplete, = self.calculate(**payload).candidates
        self.assertIn("max_piece_dimensions_m", incomplete["issues"][0]["fields"])
        self.assertIsNone(incomplete["known_cost_subtotal_rub"])

    def test_land_and_air_share_one_forecast_without_comparing_partial_totals(self):
        payload = shipment(destination="Новосибирск", allowed_modes=["road", "rail", "air"])
        payload["cargoes"][0].update(weight_tonnes=.1, volume_m3=.2)
        result = self.calculate(**payload)
        rows = result.candidates
        self.assertTrue(any(row["modes"] == ["road"] and row["complete"] for row in rows))
        self.assertTrue(any(row["modes"] == ["rail"] and "tlm-rail-2026" in row["source_ids"] for row in rows))
        self.assertTrue(any(row["complete"] and row["multimodal"] for row in rows))
        direct = next(row for row in rows if row["modes"] == ["air"])
        mixed = next(row for row in rows if row["multimodal"] and "air" in row["modes"])
        self.assertEqual(direct["forecast_price"]["air_median_rub"], 13185.27)
        self.assertIsNone(direct["total_cost_rub"])
        self.assertIsNone(mixed["total_cost_rub"])
        self.assertGreater(mixed["known_cost_subtotal_rub"], mixed["forecast_price"]["air_median_rub"])
        self.assertEqual({item["kind"] for item in mixed["cost_items"]}, {"transport"})
        self.assertIn("partial_tariff", {issue["code"] for issue in mixed["issues"]})
        self.assertTrue(recommended(result)["complete"])

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
        self.assertEqual(b["total_cost_rub"], 184426.50)
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

    def test_air_forecast_agrees_in_chat_and_main_form(self):
        payload = shipment(destination="Якутск", allowed_modes=["air"])
        cargo = payload["cargoes"][0]
        cargo.update(weight_tonnes=.1, volume_m3=.2)
        self.case.connect_ollama(self.case.domain_reply(
            "estimate_future_delivery_cost", origin="Москва", destination="Якутск",
            target_year=2030, allowed_modes=["air"], cargoes=[cargo],
        ))
        response = self.client.post("/assistant/chat", json={
            "message": "Новый расчёт. Москва → Якутск, 2030 год. Только авиа. Сколько стоит перевезти "
                       "100 кг муки в мешках по 25 кг? Общий объём 0,2 м³, партия делимая. "
                       "Размер одного мешка 0,5 × 0,4 × 0,2 м.",
        })
        self.assertEqual(response.status_code, 200, response.text)
        chat = response.json()
        self.assertEqual(chat["kind"], "calculation", chat)
        self.assertIn("сценарий с ослаблением роста", chat["reply"])
        self.client.cookies.clear()
        network = load_russia()
        payload["origin"] = network.resolve(payload["origin"])
        payload["destination"] = network.resolve(payload["destination"])
        form = self.client.post("/network/russia/forecasts", json=payload)
        self.assertEqual(form.status_code, 200, form.text)
        self.assertEqual(chat["calculation"], form.json())

    def test_expanded_air_forecast_from_regional_airport_agrees_in_chat_and_guest_form(self):
        payload = shipment(origin="Красноярск", destination="Калининград", allowed_modes=["air"])
        cargo = payload["cargoes"][0]
        cargo.update(weight_tonnes=.1, volume_m3=.25)
        self.case.connect_ollama(self.case.domain_reply(
            "estimate_future_delivery_cost", origin=payload["origin"], destination=payload["destination"],
            target_year=2030, allowed_modes=["air"], cargoes=[cargo],
        ))
        response = self.client.post("/assistant/chat", json={
            "message": "Новый расчёт. Из Красноярска в Калининград в 2030 году только авиа. "
                       "100 кг муки в мешках по 25 кг, 0,25 м³, делимая партия. "
                       "Размер одного мешка 0,5 × 0,4 × 0,2 м.",
        })
        self.assertEqual(response.status_code, 200, response.text)
        chat = response.json()
        self.assertEqual(chat["kind"], "calculation", chat)
        self.assertIn("KJA", chat["reply"])
        self.client.cookies.clear()
        form = self.client.post("/network/russia/forecasts", json=payload)
        self.assertEqual(form.status_code, 200, form.text)
        routes = form.json()["air_market"]["routes"]
        self.assertEqual(chat["calculation"]["air_market"]["routes"], routes)
        self.assertEqual(routes[0]["legs"][0]["arrival"], "KGD")
        self.assertEqual(routes[0]["legs"][0]["paid_kg"], 100)

    def test_bulky_divisible_cargo_returns_air_options_after_chat_clarification(self):
        payload = shipment(origin="Красноярск", destination="Калининград", allowed_modes=["air"])
        cargo = payload["cargoes"][0]
        cargo.update(weight_tonnes=.1, volume_m3=40, splittable=True)
        cargo.pop("max_piece_tonnes", None)
        cargo.pop("max_piece_dimensions_m", None)
        tool = "estimate_future_delivery_cost"
        self.case.connect_ollama(
            self.case.domain_reply(tool, origin=payload["origin"], destination=payload["destination"],
                                   target_year=2030, allowed_modes=["air"],
                                   cargoes=[{key: cargo[key] for key in ("id", "name", "weight_tonnes")}]),
            self.case.domain_reply(tool, cargoes=[cargo], continue_previous=True),
        )
        first = self.client.post("/assistant/chat", json={
            "message": "100 кг муки в 2030 году авиа из Красноярска в Калининград",
        }).json()
        self.assertEqual(first["kind"], "clarification", first)
        response = self.client.post("/assistant/chat", json={
            "message": "Груз упакован в мешки, партию можно разделить, 40 м³.",
            "pending": first["pending"],
        })
        self.assertEqual(response.status_code, 200, response.text)
        chat = response.json()
        self.assertEqual(chat["kind"], "calculation", chat)
        self.assertIn("14 × 476,5 кг", chat["reply"])
        self.assertIn("Санкт-Петербург (LED)", chat["reply"])
        self.assertIn("Москва (VKO)", chat["reply"])
        self.client.cookies.clear()
        form = self.client.post("/network/russia/forecasts", json=payload)
        self.assertEqual(form.status_code, 200, form.text)
        market = form.json()["air_market"]
        self.assertEqual(chat["calculation"]["air_market"], market)
        direct = market["routes"][0]["legs"][0]
        self.assertEqual(direct["shipment_count"], 14)
        self.assertLess(direct["paid_kg_per_shipment"], 500)
        self.assertAlmostEqual(direct["base_rub"], 14 * (476.5 * (153 + 18) + 370))
        for policy, split in (("verified_only", True), ("allow_assumptions", False)):
            with self.subTest(policy=policy, split=split):
                response = self.client.post("/network/russia/forecasts", json={
                    **payload, "data_policy": policy, "cargoes": [{**cargo, "splittable": split}],
                })
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertEqual(result["status"], "not_confirmed")
                self.assertEqual(result["air_market"]["routes"], [])
                self.assertEqual(result["air_market"]["weight_limits"][0]["paid_kg"], 6667)

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
        self.assertIn("184 400 ₽", chat["reply"])
        self.assertIn("от станции до станции", chat["reply"])
        self.assertTrue(any(source["url"] and "rosstat.gov.ru" in source["url"] for source in chat["sources"]))
        self.client.cookies.clear()
        network = load_russia()
        payload["origin"] = network.resolve(payload["origin"])
        payload["destination"] = network.resolve(payload["destination"])
        form = self.client.post("/network/russia/forecasts", json=payload)
        self.assertEqual(form.status_code, 200, form.text)
        self.assertEqual(chat["calculation"], form.json())

    def test_published_tlm_rates_match_between_form_and_chat_for_new_horizons(self):
        cases = (
            ("Екатеринбург", "road", 2027, 20, 20, 200000, 228600, 239808.10),
            ("Екатеринбург", "road", 2030, 20, 20, 200000, 316006.54, 332481.08),
            ("Красноярск", "rail", 2030, 100, 120, 259260, 434620.41, 438290.48),
        )
        tool = "estimate_future_delivery_cost"
        network = load_russia()
        for destination, mode, year, mass, volume, base, transport, total in cases:
            with self.subTest(destination=destination, mode=mode, year=year):
                self.client.cookies.clear()
                self.client.cookies.set(self.case.settings.cookie_name, self.case.token)
                payload = shipment(destination=destination, allowed_modes=[mode], target_year=year)
                cargo = payload["cargoes"][0]
                cargo.update(weight_tonnes=mass, volume_m3=volume)
                self.case.connect_ollama(
                    self.case.domain_reply(tool, origin="Москва", destination=destination,
                                           allowed_modes=[mode], target_year=year, cargoes=[cargo]),
                )
                mode_label = "авто" if mode == "road" else "по железной дороге"
                chat_response = self.client.post("/assistant/chat", json={
                    "message": f"Сколько стоит перевезти {mass} т муки из Москвы в {destination} "
                               f"{mode_label} в {year} году? {volume} м³, в мешках, партия делимая. "
                               "Мешки по 25 кг, 0,5 × 0,4 × 0,2 м."
                })
                self.assertEqual(chat_response.status_code, 200, chat_response.text)
                chat = chat_response.json()
                self.assertEqual(chat["kind"], "calculation", chat)
                self.client.cookies.clear()
                payload["origin"] = network.resolve(payload["origin"])
                payload["destination"] = network.resolve(payload["destination"])
                form = self.client.post("/network/russia/forecasts", json=payload)
                self.assertEqual(form.status_code, 200, form.text)
                self.assertEqual(chat["calculation"], form.json())
                calculation = chat["calculation"]
                row = next(row for row in calculation["candidates"] if row["id"] == calculation["recommendation"])
                movement = next(item for item in row["cost_items"] if item["kind"] == "transport")
                self.assertEqual(movement["pricing"]["source_ids"], [f"tlm-{mode}-2026"])
                self.assertEqual(movement["base_amount_rub"], base)
                self.assertEqual(movement["amount_rub"], transport)
                self.assertEqual(row["total_cost_rub"], total)

    def test_chat_keeps_forecast_context_with_temperature_range(self):
        tool = "estimate_future_delivery_cost"
        self.case.connect_ollama(
            self.case.domain_reply(tool, origin="Москва", destination="Новосибирск", target_year=2028,
                                   cargoes=[{"name": "Конфеты", "weight_tonnes": 100}]),
            self.case.domain_reply(tool, target_year=2026, cargoes=[{
                "name": "Конфеты", "cargo_type": "solid", "handling_profile": "careful",
                "hazardous": False, "temperature_control_required": True,
                "packaging": "boxes", "volume_m3": 180, "splittable": True,
            }]),
        )
        first = self.client.post("/assistant/chat", json={
            "message": "Сколько стоит перевезти 100 тонн конфет из Москвы в Новосибирск в 2028 году?",
        })
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["kind"], "clarification")
        response = self.client.post("/assistant/chat", json={
            "message": "Конфеты в коробках на паллетах, 180 м³, делимые, твёрдые, неопасные, бережное обращение, от +15°C до +18°C.",
            "pending": first.json()["pending"],
        })
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["kind"], "calculation")
        self.assertEqual(result["calculation"]["forecast"]["target_year"], 2028)
        self.assertEqual(result["calculation"]["request"]["cargoes"][0]["weight_tonnes"], 100)
        self.assertIsNone(result["calculation"]["recommendation"])
        self.assertIn("от +15 до +18 °C", result["reply"])

    def test_future_year_in_followup_switches_route_calculation_to_forecast(self):
        tool = "estimate_future_delivery_cost"
        cargo = {
            "name": "Конфеты", "volume_m3": 180, "packaging": "boxes", "splittable": True,
            "properties": {"phase": "solid", "hazardous": False, "handling_profile": "careful",
                           "temperature_control_required": True, "required_temperature_c": [15, 18]},
        }
        for message, previous_weight in (
            ("отправить нужно в 2030 году, вес общий 100 тонн,", {}),
            ("к 2030 году январь", {"weight_tonnes": 100}),
        ):
            with self.subTest(message=message):
                self.case.connect_ollama(self.case.domain_reply(
                    tool, cargoes=[{"weight_tonnes": 100}], continue_previous=True,
                ))
                response = self.client.post("/assistant/chat", json={
                    "message": message,
                    "pending": {"tool": "calculate_russia", "missing_fields": ["schedule"], "arguments": {
                        "origin": "Москва", "destination": "Новосибирск", "cargoes": [cargo | previous_weight],
                    }},
                })
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertEqual(result["kind"], "calculation")
                self.assertEqual(result["tool"], tool)
                calculation = result["calculation"]
                self.assertEqual(calculation["forecast"]["target_year"], 2030)
                self.assertEqual(calculation["forecast"]["date_precision"], "year")
                self.assertEqual(calculation["request"]["cargoes"][0]["weight_tonnes"], 100)
                self.assertIn("Москва → Новосибирск", result["reply"])
                self.assertIn("от +15 до +18 °C", result["reply"])

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
