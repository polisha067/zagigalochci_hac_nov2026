"""Compare three delivery schemes for a selected forecast year using the existing backend."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_YEAR = 2027


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True, help="Path to the backend repository")
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR,
                        help="Forecast year to compare")
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo.resolve()))
    from backend.a1_comparison import catalog_for, compare_year
    from backend.transport_calculator import compare_transport

    pilot = json.loads((ROOT / "pilot.json").read_text(encoding="utf-8"))
    forecast = json.loads((ROOT / "forecast.json").read_text(encoding="utf-8"))
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
            "Рейсы оплачиваются в месяце отправки; доплата до недельного минимума относится к месяцу первого дня этой недели в пределах расчётного года.",
        ],
        "catalog": catalog,
        "runs": [compare_year(compare_transport, forecast, catalog, days, args.year) for days in (14, 18)],
    }
    (ROOT / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps([{k: r[k] for k in ("deadline_days", "forecast_tonnes", "annual")}
                      for r in result["runs"]], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
