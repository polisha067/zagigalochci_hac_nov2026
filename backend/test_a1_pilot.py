from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import runpy
import unittest

from backend.transport_calculator import compare_transport


class PilotBillingTests(unittest.TestCase):
    def test_monthly_costs_and_weekly_minimum_match_annual_comparison(self):
        folder = Path(__file__).resolve().parents[1] / "a1-pilot"
        pilot = runpy.run_path(str(folder / "compare.py"))
        forecast = json.loads((folder / "forecast.json").read_text(encoding="utf-8"))
        inputs = json.loads((folder / "pilot.json").read_text(encoding="utf-8"))
        for year, expected_cost in ((2027, 24_380_000), (2030, 34_270_000)):
            with self.subTest(year=year):
                catalog = pilot["catalog_for"](forecast["route"], inputs["tariff_references"][0], year)
                result = pilot["compare_year"](compare_transport, forecast, catalog, 18, year)
                yearly = result["annual"]
                self.assertEqual(yearly["contract_road"]["cost_rub"], expected_cost)
                for scheme, annual in yearly.items():
                    monthly = [
                        row for month in result["monthly"] for row in month["schemes"]
                        if row["scheme"] == scheme
                    ]
                    self.assertEqual(
                        sum(Decimal(str(row["total_cost_rub"])) for row in monthly),
                        Decimal(str(annual["cost_rub"])),
                    )
                paid, actual = Counter(), Counter()
                for month in result["monthly"]:
                    contract = month["schemes"][1]
                    self.assertEqual(contract["total_cost_rub"], contract["paid_trips"] * 230_000)
                    for week in contract["details"]["billing_weeks"]:
                        paid[week["week_start"]] += week["paid_trips"]
                        actual[week["week_start"]] += week["actual_trips"]
                self.assertEqual(sum(paid.values()), yearly["contract_road"]["paid_road_trips"])
                self.assertTrue(all(count == max(actual[week], 2) for week, count in paid.items()))
