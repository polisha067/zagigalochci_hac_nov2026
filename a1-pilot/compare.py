"""Compare three delivery schemes for a selected forecast year using the existing backend."""
import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MSK = timezone(timedelta(hours=3))
DEFAULT_YEAR = 2027
PARTIES_PER_MONTH = 8
MIN_WEEKLY_TRIPS = 2
BASE_RATE = 260_000
CONTRACT_RATE = 230_000


def catalog_for(route, reference, year):
    if reference["rail_rub_per_kg"] != 11.2:
        raise ValueError("Verify the published rate for the assumed 40-tonne capacity")
    source = "synthetic-kemerovo-comparison"
    common = {
        "available": True, "allowed_cargo_types": ["dry"], "source_id": source,
        "valid_from": f"{year}-01-01", "valid_until": f"{year + 1}-01-31",
        "tariff_timezone": "Europe/Moscow",
    }

    def road(rate):
        return dict(common, capacity_tonnes=20, available_vehicles_per_departure=10,
                    dispatch_wait_hours=0, stop_fee_rub_per_vehicle=0, handling_rub_per_tonne=0,
                    legs=[dict(route, rate_rub_per_vehicle=rate, travel_hours=120)])

    return {
        "id": f"kemerovo-forecast-{year}", "status": "synthetic",
        "sources": [{"id": source, "status": "synthetic",
                     "description": "Условные цены и операции. Ж/д ставка 11,20 ₽/кг использована как ориентир для условной оплаты полной единицы 40 т.",
                     "url": reference["url"]}],
        "cargo_types": {"dry": {"name": "Условный сухой груз в коробках"}},
        "spot_road": road(BASE_RATE),
        "contract_road": dict(road(CONTRACT_RATE), minimum_paid_trips_per_week=MIN_WEEKLY_TRIPS,
                              fixed_cost_rub=0, billing_timezone="Europe/Moscow",
                              minimum_policy="full_iso_week"),
        "road_rail_road": dict(common, **route, minimum_batch_tonnes=25, capacity_tonnes=40,
                               max_units_per_departure=10, first_mile_capacity_tonnes=20,
                               last_mile_capacity_tonnes=20, max_first_mile_vehicles_per_departure=20,
                               max_last_mile_vehicles_per_departure=20,
                               first_mile_rub_per_vehicle=10_000, last_mile_rub_per_vehicle=10_000,
                               rail_rub_per_unit=448_000, terminal_rub_per_unit=12_000,
                               storage_rub_per_tonne_day=50, terminal_storage_rub_per_tonne_day=50,
                               before_rail_hours=24, rail_and_after_hours=252,
                               departure_mode="on_demand"),
    }


def monday(day):
    return day - timedelta(days=day.weekday())


def compare_year(compare_transport, forecast, catalog, deadline_days, target_year):
    route = forecast["route"]
    results = []
    for row in forecast["forecast_months"]:
        if not row["month"].startswith(f"{target_year}-"):
            continue
        year, month = map(int, row["month"].split("-"))
        start = datetime(year, month, 1, tzinfo=MSK)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        tonnes = Decimal(str(row["forecast_tonnes"]))
        request = {
            "dataset_id": catalog["id"],
            "route": [{"city": route[key], "service_hours": 0} for key in ("origin", "destination")],
            "cargoes": [{"id": "dry-1", "cargo_type": "dry", "splittable": True,
                         "weight_tonnes": float(tonnes / PARTIES_PER_MONTH), "load_at": 0, "unload_at": 1}],
            "schedule": {"mode": "explicit",
                         "ready_at": [(start + (end - start) * (2*i+1) / (2*PARTIES_PER_MONTH)).isoformat()
                                      for i in range(PARTIES_PER_MONTH)]},
            "max_delivery_hours": deadline_days * 24,
            "capacity_basis": "mass_only",
        }
        result = compare_transport(request, catalog)
        if abs(result["total_volume_tonnes"] - float(tonnes)) > 1e-8:
            raise ValueError(f"Shipment mass mismatch for {row['month']}")
        results.append({"month": row["month"], **result})

    if len(results) != 12:
        raise ValueError("Expected 12 forecast months")
    total_tonnes = Decimal(str(forecast["forecast_yearly_tonnes"][str(target_year)]))
    if abs(sum(r["total_volume_tonnes"] for r in results) - float(total_tonnes)) > 1e-8:
        raise ValueError("Annual volume mismatch")
    annual = {}
    for index, scheme in enumerate(("spot_road", "contract_road", "road_rail_road")):
        months = [r["schemes"][index] for r in results]
        counts = Counter()
        if scheme == "contract_road" and all("total_cost_rub" in row for row in months):
            for row in months:
                for dispatch in row["details"]["dispatch_at"]:
                    week = monday(datetime.fromisoformat(dispatch).astimezone(MSK).date())
                    counts[week] += row["details"]["vehicles_per_departure"]
            first = min(monday(date(target_year, 1, 1)), min(counts))
            last = max(monday(date(target_year, 12, 31)), max(counts))
            paid = sum(max(counts[first + timedelta(weeks=i)], MIN_WEEKLY_TRIPS)
                       for i in range((last - first).days // 7 + 1))
            cost = Decimal(paid * CONTRACT_RATE)
        else:
            paid = None
            cost = (sum(Decimal(str(row["total_cost_rub"])) for row in months)
                    if all("total_cost_rub" in row for row in months) else None)
        annual[scheme] = {
            "feasible": all(row["status"] == "feasible" for row in months),
            "cost_rub": float(cost) if cost is not None else None,
            "rub_per_tonne": round(float(cost / total_tonnes), 2) if cost is not None else None,
            "worst_delivery_hours": max((row.get("max_delivery_hours", 0) for row in months), default=0) or None,
            "physical_road_trips": sum(row.get("physical_trips", 0) for row in months),
            "rail_departures": sum(row.get("departures", 0) for row in months) if index == 2 else None,
            "paid_road_trips": paid,
            "problem_months": [r["month"] for r, row in zip(results, months) if row["status"] != "feasible"],
        }
    base = annual["spot_road"]
    for scheme in ("contract_road", "road_rail_road"):
        row = annual[scheme]
        savings = (round(base["cost_rub"] - row["cost_rub"], 2)
                   if base["cost_rub"] is not None and row["cost_rub"] is not None else None)
        row["cost_difference_vs_spot_rub"] = savings
        row["hypothesis_supported"] = base["feasible"] and row["feasible"] and savings is not None and savings > 0
    return {"deadline_days": deadline_days, "forecast_tonnes": float(total_tonnes),
            "annual": annual, "monthly": results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True, help="Path to the backend repository")
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR,
                        help="Forecast year to compare")
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo.resolve()))
    from backend.transport_calculator import compare_transport

    pilot = json.loads((ROOT / "pilot.json").read_text())
    forecast = json.loads((ROOT / "forecast.json").read_text())
    if pilot["route"] != forecast["route"] or forecast["volume_status"] != "synthetic":
        raise ValueError("Expected the synthetic pilot forecast")
    available = forecast["forecast_yearly_tonnes"]
    if str(args.year) not in available:
        parser.error(f"Year must be in the forecast. Available years: {', '.join(available)}")
    catalog = catalog_for(forecast["route"], pilot["tariff_references"][0], args.year)
    result = {
        "route": forecast["route"], "year": args.year, "volume_status": "synthetic",
        "assumptions": [
            "Одни и те же условные ставки применяются для каждого года, без индексации.",
            "8 равных партий на месяц с равномерными датами готовности внутри месяца",
            "Остатки между месяцами не объединяются, срок отсчитывается от готовности партии",
            "Договорный минимум считается один раз за каждую ISO-неделю всего года, включая крайние недели",
            "Авто и операции ж/д цепочки оценены сценарно на сопоставимой ценовой основе; фактические налоги и состав услуг требуют проверки",
            "Оплата ж/д за полную условную единицу 40 т по ориентиру 11,20 ₽/кг; это не условия оплаты перевозчика",
            "Месячные ответы содержат помесячную симуляцию; договорная годовая стоимость пересчитана по единому календарю",
        ],
        "catalog": catalog,
        "runs": [compare_year(compare_transport, forecast, catalog, days, args.year) for days in (14, 18)],
    }
    (ROOT / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps([{k: r[k] for k in ("deadline_days", "forecast_tonnes", "annual")}
                      for r in result["runs"]], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
