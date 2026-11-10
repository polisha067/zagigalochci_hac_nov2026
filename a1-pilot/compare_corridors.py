"""Evaluate the two delivery hypotheses for the synthetic A1 corridor forecasts."""
import argparse
import json
from pathlib import Path

from backend.a1_corridors import MAIN_DEADLINE_DAYS, compare_corridor, pilot_reference
from backend.a1_forecast import MAX_FORECAST_YEAR, MIN_FORECAST_YEAR, load_routes

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2030,
                        help=f"Forecast year from {MIN_FORECAST_YEAR} to {MAX_FORECAST_YEAR}")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not MIN_FORECAST_YEAR <= args.year <= MAX_FORECAST_YEAR:
        parser.error(f"Choose a year between {MIN_FORECAST_YEAR} and {MAX_FORECAST_YEAR}")
    pilot = pilot_reference()
    routes, manifest = load_routes()
    results = [compare_corridor(route, pilot["connectivity"], pilot["rail_reference"], args.year) for route in routes]
    report = {
        "year": args.year, "deadline_days": MAIN_DEADLINE_DAYS,
        "volume_status": "synthetic", "delivery_terms_status": "synthetic",
        "benchmark_version": manifest["benchmark_version"],
        "forecast_train_sha256": manifest["files_sha256"]["train.json"],
        "forecast_holdout_sha256": manifest["files_sha256"]["holdout.json"],
        "connectivity_source": pilot["connectivity_source"],
        "pilot_anchor": {key: pilot[key] for key in ("route", "connectivity", "rail_reference")},
        "results": results,
    }
    output = args.output or ROOT / f"corridor-comparison-{args.year}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
