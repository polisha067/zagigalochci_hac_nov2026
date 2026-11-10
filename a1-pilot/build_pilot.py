"""Build a fictional shipper's monthly flow, independently of freight rates."""
import json
from pathlib import Path
from random import Random

random = Random(42)
season = [0.85, 0.90, 0.98, 1.00, 1.03, 1.05, 0.95, 0.97, 1.06, 1.08, 1.10, 1.03]
history = []
for index in range(60):
    year, month = divmod(index, 12)
    tonnes = round((80 + 0.7 * index) * season[month] * random.uniform(0.94, 1.06), 1)
    history.append({
        "month": f"{2021 + year}-{month + 1:02d}",
        "tonnes": tonnes,
        "split": "train" if index < 48 else "holdout",
    })

pilot = {
    "route": {"origin": "Москва", "destination": "Кемерово"},
    "cargo": "Условный сухой груз в коробках",
    "volume_status": "synthetic",
    "volume_scope": "Один условный грузоотправитель",
    "generator": {"seed": 42, "initial_monthly_tonnes": 80,
                  "monthly_increment_tonnes": 0.7, "seasonal_factors": season,
                  "noise_multiplier_range": [0.94, 1.06]},
    "monthly_history": history,
    "tariff_references": [{
        "url": "https://www.railcontinent.ru/tarify/railway/",
        "tariff_date": "2026-11-06",
        "status": "published_reference",
        "weight_band_kg": [10000, 14999],
        "service": "Сборная перевозка, без температурного режима",
        "rail_rub_per_kg": 11.2,
        "rail_published_days": 10,
        "scope": "Строка ж/д для Кемерово; состав всей цепочки и налоговая база требуют сверки",
    }, {
        "url": "https://help.ati.su/average-rate",
        "status": "source_identified_rate_pending",
        "spot_road_rub_per_vehicle": None,
    }],
    "missing_for_comparison": ["Сопоставимая ставка ATI.SU и её параметры",
                               "Договорная ставка и оплачиваемый минимум",
                               "Состав тарифа ж/д, подвоз, перевалка и развоз",
                               "График отправок, время накопления и предельный срок"],
}
Path(__file__).with_name("pilot.json").write_text(
    json.dumps(pilot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
