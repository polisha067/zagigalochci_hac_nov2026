from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.data_sources.russia.audit_coverage import request
from backend.network.delivery_forecast import calculate_forecast
from backend.network.russia import load_russia


class PublishedLandTests(unittest.TestCase):
    def test_market_estimate_sizes_a_split_road_shipment_without_inventing_tax(self):
        quote = request(("Воронеж", "Новокузнецк"), 2030, ("road",))
        cargo = quote.cargoes[0].model_copy(update={"weight_tonnes": 100, "volume_m3": 200})
        strict = quote.model_copy(update={"cargoes": [cargo]})
        scenario = strict.model_copy(update={"data_policy": "allow_assumptions"})
        with patch.dict("os.environ", {"COVERAGE_VALHALLA_URL": ""}):
            self.assertIsNone(calculate_forecast(strict).road_market_estimate)
            budget = calculate_forecast(scenario).road_market_estimate
            indivisible = scenario.model_copy(update={"cargoes": [cargo.model_copy(update={"splittable": False})]})
            self.assertIsNone(calculate_forecast(indivisible).road_market_estimate)
        self.assertEqual((budget["trucks"], budget["forecast_road_rub"], budget["vat"]), (5, 597110.15, "unknown"))
        self.assertEqual(budget["base_unit_rub"], 75582)
        self.assertEqual(budget["assumed_m3_per_truck"], 86)

    def test_new_public_rates_reprice_without_inventing_tax_or_service(self):
        with patch.dict("os.environ", {"COVERAGE_VALHALLA_URL": ""}):
            road = calculate_forecast(request(("Самара", "Альметьевск"), 2030, ("road",)))
            rail = calculate_forecast(request(("Санкт-Петербург", "Новосибирск"), 2030, ("rail",)))
        auto, train = road.candidates[0], rail.candidates[0]
        self.assertEqual(auto["published_rate"]["published_amount_rub"], 27018)
        self.assertEqual(auto["published_rate"]["forecast_amount_rub"], 42689.32)
        self.assertIsNone(auto["known_cost_subtotal_rub"])
        self.assertEqual(train["published_rate"]["published_amount_rub"], 40000)
        self.assertEqual(train["known_cost_subtotal_rub"], 67055.53)
        for result, row in ((road, auto), (rail, train)):
            self.assertIsNone(result.recommendation)
            self.assertIsNone(row["total_cost_rub"])
            self.assertIsNone(row["max_delivery_hours"])
            self.assertFalse(row["booking_confirmed"])
            self.assertEqual(result.published_coverage["volume"]["status"], "no_directed_observations")

    def test_guest_lookup_and_forecast_use_the_same_settlement_ids(self):
        network = load_russia()
        self.assertIsNone(network.resolve("Киров"))
        origin = network.resolve("Киров, Калужская область")
        self.assertIsNotNone(origin)
        with TemporaryDirectory() as temp, patch.dict("os.environ", {"COVERAGE_VALHALLA_URL": ""}):
            with TestClient(create_app(Settings(database_path=Path(temp) / "db.sqlite3"))) as client:
                lookup = client.get("/network/russia/settlements", params={"ids": origin})
                self.assertEqual(lookup.status_code, 200)
                self.assertEqual(lookup.json()["settlements"][0]["id"], origin)
                payload = request((origin, "Калуга")).model_dump(mode="json")
                response = client.post("/network/russia/forecasts", json=payload)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["request"]["origin"], origin)
                self.assertFalse(response.json()["candidates"])
                self.assertEqual(response.json()["published_coverage"]["truck_path"]["status"], "router_unavailable")
