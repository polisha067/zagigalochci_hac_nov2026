from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import unittest

from backend.a1_comparison import catalog_for, compare_year
from backend.transport_calculator import compare_transport


class PilotBillingTests(unittest.TestCase):
    def test_monthly_costs_and_weekly_minimum_match_annual_comparison(self):
        folder = Path(__file__).resolve().parents[1] / "a1-pilot"
        forecast = json.loads((folder / "forecast.json").read_text(encoding="utf-8"))
        inputs = json.loads((folder / "pilot.json").read_text(encoding="utf-8"))
        for year, expected_cost in ((2027, 24_380_000), (2030, 34_270_000)):
            with self.subTest(year=year):
                catalog = catalog_for(forecast["route"], inputs["tariff_references"][0], year)
                result = compare_year(compare_transport, forecast, catalog, 18, year)
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

    def test_zero_forecast_months_keep_contract_minimum_without_dispatches(self):
        folder = Path(__file__).resolve().parents[1] / "a1-pilot"
        inputs = json.loads((folder / "pilot.json").read_text(encoding="utf-8"))
        year = 2100
        catalog = catalog_for(inputs["route"], inputs["tariff_references"][0], year)
        for january in (0, 80):
            with self.subTest(january=january):
                forecast = {
                    "route": inputs["route"],
                    "forecast_months": [
                        {"month": f"{year}-{month:02}", "forecast_tonnes": january if month == 1 else 0}
                        for month in range(1, 13)
                    ],
                    "forecast_yearly_tonnes": {str(year): january},
                }
                result = compare_year(compare_transport, forecast, catalog, 18, year)
                contract = result["annual"]["contract_road"]
                months = [row["schemes"][1] for row in result["monthly"]]
                self.assertEqual(contract["paid_road_trips"], 106)
                self.assertEqual(contract["cost_rub"], sum(row["total_cost_rub"] for row in months))
                self.assertIsNone(months[1]["cost_rub_per_tonne"])
                self.assertEqual(months[1]["physical_trips"], 0)
                self.assertGreater(months[1]["total_cost_rub"], 0)
                if not january:
                    self.assertIsNone(contract["rub_per_tonne"])
                    self.assertEqual(result["annual"]["spot_road"]["cost_rub"], 0)
                    self.assertEqual(result["annual"]["road_rail_road"]["cost_rub"], 0)
                    self.assertFalse(contract["hypothesis_supported"])
