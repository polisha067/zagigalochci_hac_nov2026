import copy
from datetime import datetime
import json
import unittest

from pydantic import ValidationError

from tests import BACKEND_DIR

from backend.calculation_models import CalculationRequest, CalculationResponse
from backend.schedule import build_schedule
from backend.transport_calculator import compare_transport, load_catalog


class TransportTests(unittest.TestCase):
    def setUp(self):
        root = BACKEND_DIR / "examples"
        self.request = json.loads((root / "calculation-request.json").read_text(encoding="utf-8"))
        self.catalog = load_catalog(root / "catalog.json")

    def calculate(self):
        return compare_transport(self.request, self.catalog)

    def once(self):
        self.request["schedule"] = {"mode": "once", "ready_at": "2026-11-09T09:00:00+03:00"}

    def explicit(self, dates):
        self.request["schedule"] = {"mode": "explicit", "ready_at": dates}

    def test_manual_baseline_and_response_contract(self):
        result = self.calculate()
        self.assertEqual((result["shipments"], result["total_volume_tonnes"]), (8, 80))
        self.assertEqual([s["total_cost_rub"] for s in result["schemes"]], [240000, 208000, 140000])
        self.assertEqual(result["schemes"][2]["max_delivery_hours"], 156)
        self.assertEqual(result["recommendation"], "contract_road")
        self.assertEqual(result["schemes"][1]["savings_vs_base_rub"], 32000)
        self.assertEqual(result["schemes"][1]["savings_vs_base_percent"], 13.33)
        CalculationResponse.model_validate(result)
        self.assertEqual(sum(week["tonnes"] for week in result["volume_by_week"]), 80)

    def test_once_is_one_shipment_not_one_per_week(self):
        self.once()
        result = self.calculate()
        self.assertEqual((result["shipments"], result["total_volume_tonnes"]), (1, 10))
        self.assertEqual(result["schemes"][0]["total_cost_rub"], 30000)
        self.assertEqual(result["schemes"][1]["total_cost_rub"], 52000)
        self.assertEqual(result["recommendation"], "spot_road")

    def test_every_two_weeks_bills_empty_contract_weeks(self):
        self.request["schedule"]["frequency"] = {"count": 1, "every": 2, "unit": "week"}
        result = self.calculate()
        self.assertEqual(result["shipments"], 2)
        self.assertEqual(result["schemes"][1]["paid_trips"], 8)
        self.assertEqual(len(result["schemes"][1]["details"]["billing_weeks"]), 4)

    def test_partial_period_and_exclusive_end(self):
        self.request["schedule"]["end_at"] = "2026-11-12T12:00:00+03:00"
        self.assertEqual(self.calculate()["shipments"], 1)
        self.request["schedule"]["end_at"] = "2026-11-12T12:00:01+03:00"
        self.assertEqual(self.calculate()["shipments"], 2)

    def test_months_use_calendar_and_preserve_original_day(self):
        self.request["schedule"] = {"mode": "regular", "start_at": "2027-01-31T09:00:00+03:00", "end_at": "2027-04-01T00:00:00+03:00", "frequency": {"count": 1, "unit": "month"}}
        result = self.calculate()
        self.assertEqual([value[:10] for value in result["ready_at"]], ["2027-01-31", "2027-02-28", "2027-03-31"])

    def test_daily_frequency(self):
        self.request["schedule"] = {"mode": "regular", "start_at": "2026-11-09T00:00:00Z", "end_at": "2026-11-11T00:00:00Z", "frequency": {"count": 2, "unit": "day"}}
        result = self.calculate()
        self.assertEqual(result["shipments"], 4)
        self.assertEqual(result["ready_at"][1], "2026-11-09T12:00:00+00:00")

    def test_invalid_dates_and_unknown_fields(self):
        bad_schedules = [
            {"mode": "once", "ready_at": "2026-11-09T09:00:00"},
            {"mode": "once", "ready_at": 123},
            {"mode": "once", "ready_at": "9999-12-31T23:59:59Z"},
            {"mode": "explicit", "ready_at": []},
            {"mode": "explicit", "ready_at": ["2026-11-09T09:00:00Z", "2026-11-09T12:00:00+03:00"]},
            {"mode": "regular", "start_at": "2026-11-10T00:00:00Z", "end_at": "2026-11-09T00:00:00Z", "frequency": {"count": 1, "unit": "week"}},
        ]
        for schedule in bad_schedules:
            with self.subTest(schedule=schedule), self.assertRaises(ValidationError):
                CalculationRequest.model_validate(dict(self.request, schedule=schedule))
        with self.assertRaises(ValidationError):
            CalculationRequest.model_validate(dict(self.request, stops=["Калуга"]))

    def test_too_many_shipments_are_rejected_before_costing(self):
        self.request["schedule"]["frequency"] = {"count": 168, "unit": "day"}
        with self.assertRaisesRegex(ValueError, "512"):
            self.calculate()

    def test_invalid_numbers_are_not_coerced(self):
        for value in (0, -1, True, "10", float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.request["cargoes"][0]["weight_tonnes"] = value
                self.calculate()
        self.request["cargoes"][0]["weight_tonnes"] = 10
        for value in (0, -1, True, "2", float("nan")):
            with self.subTest(deadline=value), self.assertRaises(ValidationError):
                self.request["max_delivery_hours"] = value
                self.calculate()

    def test_fractional_deadline_and_no_recommendation(self):
        self.request["max_delivery_hours"] = 0.5
        result = self.calculate()
        self.assertIsNone(result["recommendation"])
        self.assertTrue(all(item["status"] != "feasible" for item in result["schemes"]))

    def test_capacity_boundary_and_money_components(self):
        self.once()
        for weight, trips, cost in [(20, 1, 30000), (21, 2, 60000)]:
            with self.subTest(weight=weight):
                self.request["cargoes"][0]["weight_tonnes"] = weight
                spot = self.calculate()["schemes"][0]
                self.assertEqual((spot["physical_trips"], spot["total_cost_rub"]), (trips, cost))
                self.assertEqual(sum(spot["breakdown_rub"].values()), cost)

    def test_decimal_weights_do_not_add_phantom_vehicle(self):
        self.once()
        self.request["cargoes"][0]["weight_tonnes"] = 0.1
        self.request["cargoes"].append(dict(self.request["cargoes"][0], id="second", weight_tonnes=0.2))
        self.catalog["spot_road"]["capacity_tonnes"] = 0.3
        self.assertEqual(self.calculate()["schemes"][0]["physical_trips"], 1)

    def test_volume_can_be_binding_constraint(self):
        self.once()
        self.request["capacity_basis"] = "mass_and_volume"
        self.request["cargoes"][0].update(weight_tonnes=1, volume_m3=81)
        self.assertEqual(self.calculate()["schemes"][0]["physical_trips"], 2)
        del self.request["cargoes"][0]["volume_m3"]
        with self.assertRaises(ValidationError):
            self.calculate()

    def test_mass_only_is_explicit_and_warns(self):
        result = self.calculate()
        self.assertTrue(any("только по массе" in warning for warning in result["warnings"]))
        del self.request["capacity_basis"]
        with self.assertRaises(ValidationError):
            self.calculate()

    def test_mass_only_does_not_ignore_known_volume(self):
        self.once()
        self.request["cargoes"][0].update(weight_tonnes=1, volume_m3=81)
        self.assertEqual(self.calculate()["schemes"][0]["physical_trips"], 2)

    def test_intermediate_unload_and_load_use_segment_maximum(self):
        self.request = json.loads((BACKEND_DIR / "examples/calculation-stops.json").read_text())
        result = self.calculate()
        self.assertEqual(result["total_volume_tonnes"], 25)
        self.assertEqual([row["weight_tonnes"] for row in result["load_profile"]], [10, 15])
        spot = result["schemes"][0]
        self.assertEqual((spot["physical_trips"], spot["total_cost_rub"]), (1, 35000))
        self.assertEqual(spot["details"]["cargo_delivery_hours"], {"flour-1": 11, "sugar-1": 24})
        self.assertEqual(result["schemes"][2]["status"], "insufficient_data")

    def test_extra_stop_requires_leg_quotes(self):
        self.request["route"].insert(1, {"city": "Неизвестный пункт", "service_hours": 5})
        self.request["cargoes"][0]["unload_at"] = 2
        result = self.calculate()
        self.assertIsNone(result["recommendation"])
        self.assertTrue(all(s["status"] == "insufficient_data" for s in result["schemes"]))

    def test_bad_load_indices_duplicate_ids_and_duplicate_cities(self):
        bad = copy.deepcopy(self.request)
        bad["cargoes"][0]["load_at"] = 1
        with self.assertRaises(ValidationError):
            compare_transport(bad, self.catalog)
        bad = copy.deepcopy(self.request)
        bad["cargoes"].append(dict(bad["cargoes"][0]))
        with self.assertRaises(ValidationError):
            compare_transport(bad, self.catalog)
        bad = copy.deepcopy(self.request)
        bad["route"][1]["city"] = " мОСКВА "
        with self.assertRaises(ValidationError):
            compare_transport(bad, self.catalog)

    def test_incompatible_and_unknown_cargoes_are_not_approved(self):
        self.request["cargoes"].append(dict(self.request["cargoes"][0], id="sugar", cargo_type="sugar"))
        self.catalog["compatibility"]["flour|sugar"] = False
        self.assertTrue(all(s["status"] == "infeasible" for s in self.calculate()["schemes"]))
        del self.catalog["compatibility"]["flour|sugar"]
        self.assertTrue(all(s["status"] == "insufficient_data" for s in self.calculate()["schemes"]))
        self.request["cargoes"][1]["cargo_type"] = "unknown"
        self.assertTrue(all(s["status"] == "insufficient_data" for s in self.calculate()["schemes"]))

    def test_indivisible_oversize_and_unproven_packing(self):
        self.request["cargoes"][0].update(weight_tonnes=21, splittable=False)
        self.assertEqual(self.calculate()["schemes"][0]["status"], "infeasible")
        self.request["cargoes"][0]["weight_tonnes"] = 15
        self.request["cargoes"].append(dict(self.request["cargoes"][0], id="second"))
        self.assertEqual(self.calculate()["schemes"][0]["status"], "insufficient_data")

    def test_no_available_vehicle_and_no_rate(self):
        self.catalog["spot_road"]["available_vehicles_per_departure"] = 0
        self.assertEqual(self.calculate()["schemes"][0]["status"], "infeasible")
        del self.catalog["contract_road"]["legs"][0]["rate_rub_per_vehicle"]
        contract = self.calculate()["schemes"][1]
        self.assertEqual(contract["status"], "insufficient_data")
        self.assertNotIn("total_cost_rub", contract)

    def test_expired_tariff_and_missing_source(self):
        self.catalog["spot_road"]["valid_until"] = "2026-11-01"
        self.assertEqual(self.calculate()["schemes"][0]["status"], "insufficient_data")
        self.catalog["contract_road"]["source_id"] = "missing"
        self.assertEqual(self.calculate()["schemes"][1]["status"], "insufficient_data")

    def test_delayed_dispatch_can_add_contract_week(self):
        self.explicit(["2026-11-15T23:00:00+03:00"])
        self.catalog["contract_road"]["dispatch_wait_hours"] = 2
        contract = self.calculate()["schemes"][1]
        self.assertEqual(contract["paid_trips"], 4)
        self.assertEqual(contract["max_delivery_hours"], 26)

    def test_free_base_does_not_divide_by_zero(self):
        self.catalog["spot_road"]["legs"][0]["rate_rub_per_vehicle"] = 0
        result = self.calculate()
        self.assertEqual(result["recommendation"], "spot_road")
        self.assertNotIn("savings_vs_base_percent", result["schemes"][0])

    def test_rail_can_be_recommended_and_deadline_equality_is_allowed(self):
        self.request["max_delivery_hours"] = 156
        result = self.calculate()
        self.assertEqual(result["recommendation"], "road_rail_road")
        self.request["max_delivery_hours"] = 155.999
        self.assertEqual(self.calculate()["schemes"][2]["status"], "infeasible")

    def test_rail_remainder_is_included_not_dropped(self):
        self.explicit(["2026-11-09T00:00:00+03:00", "2026-11-12T12:00:00+03:00", "2026-11-16T00:00:00+03:00"])
        self.request["max_delivery_hours"] = 250
        rail = self.calculate()["schemes"][2]
        self.assertEqual(rail["status"], "feasible")
        self.assertEqual(sum(b["shipments"] for b in rail["details"]["batches"]), 3)
        self.assertEqual(sum(b["weight_tonnes"] for b in rail["details"]["batches"]), 30)
        self.assertEqual(rail["total_cost_rub"], 70000)

    def test_rail_timetable_and_storage(self):
        self.request["max_delivery_hours"] = 200
        terms = self.catalog["road_rail_road"]
        terms.update(departure_mode="interval", departure_every_hours=24, departure_anchor="2026-11-09T00:00:00+03:00", storage_rub_per_tonne_day=10, terminal_storage_rub_per_tonne_day=20)
        rail = self.calculate()["schemes"][2]
        self.assertEqual(rail["max_delivery_hours"], 162)
        self.assertEqual(rail["breakdown_rub"]["accumulation_storage"], 1400)
        self.assertEqual(rail["breakdown_rub"]["terminal_storage"], 400)
        self.assertEqual(rail["total_cost_rub"], 141800)

    def test_origin_handling_can_miss_rail_departure(self):
        self.request["max_delivery_hours"] = 200
        self.request["route"][0]["service_hours"] = 7
        self.catalog["road_rail_road"].update(departure_mode="interval", departure_every_hours=24, departure_anchor="2026-11-09T00:00:00+03:00")
        rail = self.calculate()["schemes"][2]
        self.assertEqual(rail["max_delivery_hours"], 186)

    def test_rail_unavailable_or_missing_timetable(self):
        self.catalog["road_rail_road"]["available"] = False
        self.assertEqual(self.calculate()["schemes"][2]["status"], "infeasible")
        self.catalog["road_rail_road"]["available"] = True
        self.catalog["road_rail_road"]["departure_mode"] = "interval"
        self.assertEqual(self.calculate()["schemes"][2]["status"], "insufficient_data")

    def test_rail_capacity_is_summed_for_same_departure(self):
        self.explicit(["2026-11-09T01:00:00+03:00", "2026-11-09T02:00:00+03:00"])
        self.request["cargoes"][0]["weight_tonnes"] = 20
        self.request["max_delivery_hours"] = 200
        self.catalog["road_rail_road"].update(max_units_per_departure=1, departure_mode="interval", departure_every_hours=24, departure_anchor="2026-11-09T00:00:00+03:00")
        self.assertEqual(self.calculate()["schemes"][2]["status"], "infeasible")

    def test_switching_handling_profile_requires_cleaning_plan(self):
        self.request = json.loads((BACKEND_DIR / "examples/calculation-stops.json").read_text())
        self.catalog["cargo_types"]["sugar"]["handling_profile"] = "different_profile"
        result = self.calculate()["schemes"][0]
        self.assertEqual(result["status"], "insufficient_data")
        self.assertIn("cleaning_transition_plan", result["missing_fields"])

    def test_zero_and_unknown_available_capacity_differ(self):
        self.catalog["road_rail_road"]["max_units_per_departure"] = 0
        self.assertEqual(self.calculate()["schemes"][2]["status"], "infeasible")
        del self.catalog["road_rail_road"]["max_units_per_departure"]
        self.assertEqual(self.calculate()["schemes"][2]["status"], "insufficient_data")

    def test_inputs_preserved(self):
        before = copy.deepcopy((self.request, self.catalog))
        self.calculate()
        self.assertEqual((self.request, self.catalog), before)


if __name__ == "__main__":
    unittest.main()
