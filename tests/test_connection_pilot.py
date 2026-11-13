import unittest
from decimal import Decimal

from pydantic import ValidationError

from backend.network.connection_pilot import ConnectionConditions, evaluate_connection
from backend.network.delivery_forecast import ForecastRequest, calculate_forecast
from tests.test_delivery_forecast import shipment


class ConnectionPilotTests(unittest.TestCase):
    def test_cutoff_is_inclusive_and_unknown_slot_leaves_the_quote_partial(self):
        conditions = ConnectionConditions(free_truck_wait_hours=1, transfer_rub=0,
                                          truck_wait_rub_per_hour=100)
        at_cutoff = evaluate_connection(Decimal(1000), conditions, "requested", Decimal(4))
        self.assertFalse(at_cutoff["missed"])
        self.assertEqual(at_cutoff["buffer_remaining_hours"], 0)
        self.assertEqual(at_cutoff["total_rub"], 1300)
        for next_slot in (None, Decimal(5)):
            settings = conditions.model_copy(update={"next_slot_hours": next_slot})
            missed = evaluate_connection(Decimal(1000), settings, "requested", Decimal(6))
            self.assertTrue(missed["missed"])
            self.assertIsNone(missed["extra_hours"])
            self.assertIsNone(missed["total_rub"])
            self.assertIn("next_slot", missed["missing"])
            self.assertEqual(missed["known_rub"], 1300)

    def test_storage_rounds_up_only_after_free_time(self):
        conditions = ConnectionConditions(buffer_hours=0, next_slot_hours=32, free_storage_hours=2,
                                          transfer_rub=500, storage_rub_per_day=200, rebooking_rub=100)
        first = evaluate_connection(Decimal(1000), conditions, "requested", Decimal(6))
        second = evaluate_connection(Decimal(1000), conditions, "requested", Decimal("5.99"))
        self.assertEqual((first["billable_storage_days"], first["total_rub"]), (1, 1800))
        self.assertEqual((second["billable_storage_days"], second["total_rub"]), (2, 2000))
        with self.assertRaises(ValidationError):
            ConnectionConditions(transfer_rub=-1)

    def test_forecast_exposes_scenarios_only_for_pilot_candidate(self):
        payload = shipment(destination="Красноярск", allowed_modes=["rail", "road"],
                           stops=[{"settlement": "Екатеринбург", "action": "visit", "dwell_hours": 2}],
                           connection_conditions={"next_slot_hours": 30, "transfer_rub": 12000,
                                                  "free_storage_hours": 6,
                                                  "truck_wait_rub_per_hour": 1000, "storage_rub_per_day": 500,
                                                  "rebooking_rub": 0})
        result = calculate_forecast(ForecastRequest.model_validate(payload))
        row, = [candidate for candidate in result.candidates if "connection_pilot" in candidate]
        self.assertEqual(row["modes"], ["rail", "road"])
        pilot = row["connection_pilot"]
        market = pilot["market_budget"]["rates"]
        self.assertEqual(market["transfer_rub"]["base_amount_rub"], 40870)
        self.assertGreater(market["transfer_rub"]["forecast_amount_rub"], 40870)
        self.assertEqual(market["storage_rub_per_day"]["base_year"], 2023)
        self.assertNotIn("rebooking_rub", market)
        self.assertEqual(pilot["movement_rub"], 222409.43)
        self.assertEqual(pilot["truck_10t_forecast_rub"], 134410.22)
        self.assertEqual(row["total_cost_rub"], 233011.84)
        delayed = next(scenario for scenario in pilot["scenarios"] if scenario["scenario"] == "requested")
        self.assertEqual(delayed["extra_hours"], 30)
        self.assertEqual(delayed["total_rub"], 238909.43)
        self.assertEqual(delayed["charges_rub"],
                         {"transfer": 12000, "truck_wait": 4000, "storage": 500, "rebooking": 0})
        alternatives = {row["scenario"]: row for row in pilot["scenarios"]}
        self.assertEqual(alternatives["pickup_after_6h"]["total_rub"], 238409.43)
        self.assertEqual(alternatives["pickup_after_24h"]["total_rub"], 238909.43)

    def test_scenario_pickups_are_available_without_confirmed_window(self):
        payload = shipment(destination="Красноярск", allowed_modes=["rail", "road"],
                           stops=[{"settlement": "Екатеринбург", "action": "visit", "dwell_hours": 2}])
        result = calculate_forecast(ForecastRequest.model_validate(payload))
        pilot, = [row["connection_pilot"] for row in result.candidates if "connection_pilot" in row]
        rows = {row["scenario"]: row for row in pilot["scenarios"]}
        self.assertIsNone(rows["requested"]["extra_hours"])
        for name, expected in (("pickup_after_6h", 12), ("pickup_after_24h", 30)):
            with self.subTest(name=name):
                row = rows[name]
                self.assertEqual(row["extra_hours"], expected)
                self.assertEqual(row["billable_storage_days"], 1)
                self.assertEqual(row["billable_truck_hours"], 4)
                self.assertNotIn("next_slot", row["missing"])
                self.assertIsNone(row["charges_rub"]["storage"])
                self.assertIsNone(row["total_rub"])
