"""Freeze 2025 predictions before opening the holdout, then forecast a selected year."""
import argparse
import hashlib
import json
from pathlib import Path

from forecast import metrics
from backend.a1_forecast import (
    BENCHMARK, HOLDOUT_MONTHS, MAX_FORECAST_YEAR, MIN_FORECAST_YEAR,
    START, TRAIN_MONTHS, fit, month_label, read_snapshot, series_by_route,
)

ROOT = Path(__file__).resolve().parent
def encode(data):
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2030)
    args = parser.parse_args()
    if not MIN_FORECAST_YEAR <= args.year <= MAX_FORECAST_YEAR:
        parser.error(f"Choose a forecast year between {MIN_FORECAST_YEAR} and {MAX_FORECAST_YEAR}")

    manifest = json.loads((BENCHMARK / "manifest.json").read_bytes())
    routes = read_snapshot("routes.json", manifest)
    ids = [route["route_id"] for route in routes]
    train = series_by_route(read_snapshot("train.json", manifest), ids, 0, TRAIN_MONTHS)
    predictions = {}
    for route_id, values in train.items():
        predict = fit(values)
        predictions[route_id] = {
            "model": [predict(i) for i in range(TRAIN_MONTHS, TRAIN_MONTHS + HOLDOUT_MONTHS)],
            "baseline": values[-12:],
        }

    frozen = encode({
        "method": "linear_trend_with_monthly_effects",
        "train_sha256": manifest["files_sha256"]["train.json"],
        "train_through": "2024-12",
        "months": [month_label(START, i) for i in range(48, 60)],
        "predictions": predictions,
    })
    frozen_path = ROOT / "routes-predictions-2025.json"
    try:
        with frozen_path.open("xb") as stream:
            stream.write(frozen)
    except FileExistsError:
        if frozen_path.read_bytes() != frozen:
            raise ValueError("Existing frozen predictions differ")

    holdout = series_by_route(read_snapshot("holdout.json", manifest), ids, TRAIN_MONTHS, HOLDOUT_MONTHS)
    report = []
    all_actual, all_model, all_baseline = [], [], []
    year_start = (args.year - 2021) * 12
    for route in routes:
        route_id = route["route_id"]
        actual = holdout[route_id]
        model = predictions[route_id]["model"]
        baseline = predictions[route_id]["baseline"]
        all_actual.extend(actual)
        all_model.extend(model)
        all_baseline.extend(baseline)
        predict = fit(train[route_id] + actual)
        months = [
            {"month": month_label(START, i), "forecast_tonnes": predict(i)}
            for i in range(year_start, year_start + 12)
        ]
        report.append({
            "route_id": route_id,
            "origin": route["origin"],
            "destination": route["destination"],
            "holdout_model": metrics(actual, model),
            "holdout_baseline": metrics(actual, baseline),
            "forecast_tonnes": round(sum(row["forecast_tonnes"] for row in months), 1),
            "forecast_months": months,
        })

    summary = {
        "volume_status": manifest["volume_status"],
        "forecast_year": args.year,
        "predictions_2025_sha256": hashlib.sha256(frozen).hexdigest(),
        "pooled_holdout_model": metrics(all_actual, all_model),
        "pooled_holdout_baseline": metrics(all_actual, all_baseline),
        "forecast_total_tonnes": round(sum(row["forecast_tonnes"] for row in report), 1),
    }
    output = ROOT / f"routes-forecast-{args.year}.json"
    output.write_bytes(encode({**summary, "routes": report}))
    print(encode(summary).decode(), end="")
    for row in report:
        print(row["origin"], "→", row["destination"], row["holdout_model"]["wape_percent"],
              row["holdout_baseline"]["wape_percent"], row["forecast_tonnes"])


if __name__ == "__main__":
    main()
