import copy
import json
from pathlib import Path
import unittest

from calculator import compare_delivery


class CalculatorTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parent / "examples"
        self.request = json.loads((root / "request.json").read_text(encoding="utf-8"))
        self.dataset = json.loads((root / "dataset.json").read_text(encoding="utf-8"))

    def test_manual_examples_and_capacity_boundary(self):
        for weight, volume, trips, spot, contract in [
            (10, 80, 8, 240000, 208000),
            (20, 160, 8, 240000, 208000),
            (21, 168, 16, 480000, 416000),
        ]:
            with self.subTest(weight=weight):
                result = compare_delivery(dict(self.request, batch_tonnes=weight), self.dataset)
                self.assertEqual(result["total_volume_tonnes"], volume)
                self.assertEqual(result["schemes"][0]["physical_trips"], trips)
                self.assertEqual(result["schemes"][0]["total_cost_rub"], spot)
                self.assertEqual(result["schemes"][1]["total_cost_rub"], contract)

    def test_contract_minimum_changes_choice(self):
        result = compare_delivery(dict(self.request, shipments_per_week=1), self.dataset)
        self.assertEqual(result["recommendation"], "spot_road")
        self.assertEqual(result["schemes"][1]["paid_trips"], 8)

    def test_deadline_excludes_cheapest_option(self):
        self.dataset["contract_road"]["delivery_days"] = 3
        result = compare_delivery(self.request, self.dataset)
        self.assertEqual(result["recommendation"], "spot_road")
        result = compare_delivery(dict(self.request, max_delivery_days=0.5), self.dataset)
        self.assertIsNone(result["recommendation"])

    def test_missing_rate_is_not_free_delivery(self):
        del self.dataset["contract_road"]["rate_rub_per_trip"]
        result = compare_delivery(self.request, self.dataset)
        self.assertEqual(result["schemes"][1]["status"], "insufficient_data")
        self.assertNotIn("total_cost_rub", result["schemes"][1])
        self.assertEqual(result["recommendation"], "spot_road")

    def test_invalid_frequency(self):
        for value in [None, 0, -1, True, 1.5, float("nan")]:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "shipments_per_week"):
                compare_delivery(dict(self.request, shipments_per_week=value), self.dataset)

    def test_wrong_route_rejected(self):
        with self.assertRaisesRegex(ValueError, "destination"):
            compare_delivery(dict(self.request, destination="Казань"), self.dataset)

    def test_inputs_are_preserved(self):
        before = copy.deepcopy((self.request, self.dataset))
        compare_delivery(self.request, self.dataset)
        self.assertEqual((self.request, self.dataset), before)


if __name__ == "__main__":
    unittest.main()
