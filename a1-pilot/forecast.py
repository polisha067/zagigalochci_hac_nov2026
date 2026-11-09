"""Validate on the last 12 months, then forecast through a selected year."""
import argparse
import json
import sys
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

from backend.a1_forecast import MAX_FORECAST_YEAR, fit, month_label


def metrics(actual, predicted):
    errors = [abs(a - p) for a, p in zip(actual, predicted)]
    return {
        "mae_tonnes": round(mean(errors), 2),
        "wape_percent": round(100 * sum(errors) / sum(actual), 2),
        "actual_total_tonnes": round(sum(actual), 1),
        "predicted_total_tonnes": round(sum(predicted), 1),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--through-year", type=int, default=2030,
                        help="Last calendar year in the forecast")
    args = parser.parse_args()
    pilot = json.loads((ROOT / "pilot.json").read_text(encoding="utf-8"))
    rows = pilot["monthly_history"]
    start = rows[0]["month"]
    values = [row["tonnes"] for row in rows]
    split = len(rows) - 12
    if split < 24 or any(
        row["month"] != month_label(start, i)
        or row["split"] != ("train" if i < split else "holdout")
        for i, row in enumerate(rows)
    ) or any(v <= 0 for v in values):
        raise ValueError("Expected consecutive positive monthly values and a 12-month holdout")

    predict = fit(values[:split])
    held_out = [predict(i) for i in range(split, len(values))]
    baseline = values[split - 12:split]
    first_forecast_year = int(rows[-1]["month"][:4]) + 1
    if rows[-1]["month"][5:] != "12":
        parser.error("The historical series must end in December")
    if not first_forecast_year <= args.through_year <= MAX_FORECAST_YEAR:
        parser.error(f"Forecast year must be between {first_forecast_year} and {MAX_FORECAST_YEAR}")
    forecast_month_count = 12 * (args.through_year - first_forecast_year + 1)
    predict_full = fit(values)
    future = [
        {"month": month_label(start, i), "forecast_tonnes": predict_full(i)}
        for i in range(len(values), len(values) + forecast_month_count)
    ]
    result = {
        "route": pilot["route"],
        "volume_status": pilot["volume_status"],
        "method": "linear_trend_with_monthly_effects",
        "holdout": {
            "train_through": rows[split - 1]["month"],
            "model": metrics(values[split:], held_out),
            "baseline_same_month_previous_year": metrics(values[split:], baseline),
            "months": [
                {"month": row["month"], "synthetic_actual_tonnes": row["tonnes"],
                 "forecast_tonnes": forecast, "baseline_tonnes": reference}
                for row, forecast, reference in zip(rows[split:], held_out, baseline)
            ],
        },
        "refit_through": rows[-1]["month"],
        "forecast_months": future,
        "forecast_yearly_tonnes": {
            year: round(sum(row["forecast_tonnes"] for row in future
                            if row["month"].startswith(year + "-")), 1)
            for year in sorted({row["month"][:4] for row in future})
        },
    }
    (ROOT / "forecast.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**{key: result["holdout"][key] for key in (
        "model", "baseline_same_month_previous_year")}, "forecast": result["forecast_yearly_tonnes"]},
        ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
