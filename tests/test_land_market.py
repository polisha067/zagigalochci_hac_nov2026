import unittest
from unittest.mock import patch

from backend.data_sources.russia.audit_coverage import request
from backend.network.delivery_forecast import calculate_forecast
from backend.network.russia import load_russia


class LandMarketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network = load_russia()

    def calculate(self, route, policy="allow_assumptions", year=2030):
        query = request(route, year).model_copy(update={"data_policy": policy})
        with patch.dict("os.environ", {"COVERAGE_VALHALLA_URL": ""}):
            return calculate_forecast(query, self.network)

    def test_known_freight_corridor_has_modeled_rail_and_published_road(self):
        route = ("Владивосток", "Казань")
        market = self.calculate(route).land_market
        legs = market["legs"][0]
        rail = [leg for leg in legs if leg["mode"] == "rail"]
        self.assertEqual({leg["equipment"] for leg in rail}, {"Контейнер 20 футов", "Контейнер 40 футов"})
        self.assertTrue(all(leg["kind"] == "model" and leg["service"]["days"] == 11 for leg in rail))
        self.assertTrue(any(leg["mode"] == "road" and leg["kind"] == "published_base" for leg in legs))
        self.assertIsNone(self.calculate(route, policy="verified_only").land_market)
        earlier = self.calculate(route, year=2028).land_market["legs"][0]
        self.assertLess(earlier[1]["forecast_rub"], rail[0]["forecast_rub"])

    def test_stops_keep_their_order_and_prices_expose_their_basis(self):
        market = self.calculate(("Пермь", "Ижевск", "Казань")).land_market
        road = next(row for row in market["routes"] if all(leg["mode"] == "road" for leg in row["legs"]))
        self.assertEqual([(leg["origin"], leg["destination"], leg["kind"]) for leg in road["legs"]],
                         [("Пермь", "Ижевск", "published_base"), ("Ижевск", "Казань", "model")])
        self.assertEqual(road["vat"], "unknown")
        self.assertAlmostEqual(road["movement_rub"], sum(leg["forecast_rub"] for leg in road["legs"]), places=2)

    def test_known_rail_tariff_wins_over_model_on_same_corridor(self):
        legs = self.calculate(("Москва", "Благовещенск, Амурская область")).land_market["legs"][0]
        rail = [leg for leg in legs if leg["mode"] == "rail"]
        self.assertEqual({leg["equipment"] for leg in rail}, {"Контейнер 20 футов", "Контейнер 40 футов"})
        self.assertTrue(any(leg["scope"] == "door_to_door" for leg in rail))
        self.assertTrue(all(leg["kind"] == "published_base" for leg in rail))

    def test_container_haulage_after_rail_is_a_separate_station_reference(self):
        market = self.calculate(("Москва", "Новосибирск", "Томск")).land_market
        refs = market["terminal_references"]
        self.assertEqual({(row["station"], row["destination"], row["size_ft"]) for row in refs},
                         {("Клещиха", "Томск", 20), ("Клещиха", "Томск", 40)})
        self.assertTrue(all(row["vat"] == "unknown" and row["receive_rub"] > 17500 for row in refs))
        mixed = [row for row in market["routes"] if row["transfer_count"]]
        self.assertTrue(mixed)
        self.assertTrue(all(row["movement_rub"] is None for row in mixed))

    def test_rail_transfer_prices_each_leg_and_keeps_quote_unconfirmed(self):
        query = request(("Новосибирск", "Казань"), modes=("rail",)).model_copy(
            update={"data_policy": "allow_assumptions"})
        result = calculate_forecast(query, self.network)
        options = [row for row in result.land_market["routes"] if row.get("rail_transfer") == "Владивосток"]
        self.assertTrue(options)
        for row in options:
            left, right = row["legs"]
            self.assertEqual(left["destination"], right["origin"])
            self.assertEqual(left["equipment"], right["equipment"])
            self.assertIn("fesco.ru", row["rail_transfer_url"])
