"""Freeze a synthetic multi-route benchmark before fitting any forecast."""
import argparse
import hashlib
import json
import math
from pathlib import Path
from random import Random

CORRIDORS = (
    ("Москва", "Санкт-Петербург", "Между двумя крупнейшими городскими узлами"),
    ("Москва", "Ростов-на-Дону", "Связь Центральной России с югом"),
    ("Краснодар", "Новороссийск", "Короткое плечо к морскому порту"),
    ("Ростов-на-Дону", "Махачкала", "Юг и Северный Кавказ"),
    ("Архангельск", "Вологда", "Европейский Север"),
    ("Нижний Новгород", "Казань", "Перевозка между крупными узлами Поволжья"),
    ("Уфа", "Екатеринбург", "Переход из Поволжья на Урал"),
    ("Челябинск", "Омск", "Переход с Урала в Западную Сибирь"),
    ("Новосибирск", "Красноярск", "Межрегиональное плечо внутри Сибири"),
    ("Иркутск", "Улан-Удэ", "Коридор через Прибайкалье"),
    ("Красноярск", "Хабаровск", "Длинное плечо между Сибирью и Дальним Востоком"),
    ("Хабаровск", "Владивосток", "Дальневосточный коридор к порту"),
)
SEED = 826041
FIRST_YEAR = 2021
TRAIN_MONTHS = 48
HOLDOUT_MONTHS = 12


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def select_routes(snapshot):
    cities = {city["name"]: city for city in snapshot["settlements"]}
    pairs = {
        (cities[origin]["id"], cities[destination]["id"]): (origin, destination, purpose)
        for origin, destination, purpose in CORRIDORS
    }
    links = {pair: {} for pair in pairs}
    for origin, destination, mode, km, hours, reason in snapshot["connections"]:
        pair = (origin, destination)
        if pair in links and mode in ("road", "rail") and not reason and km and hours:
            if km > 0 and hours > 0:
                links[pair][mode] = {"km": km, "hours": hours}
    for pair, (origin, destination, purpose) in pairs.items():
        modes = links[pair]
        if modes.keys() != {"road", "rail"}:
            raise ValueError(f"Missing road or rail reference for {origin} → {destination}")
        yield {
            "route_id": "-".join(pair),
            "origin": origin,
            "destination": destination,
            "origin_region": cities[origin]["region"],
            "destination_region": cities[destination]["region"],
            "coverage_case": purpose,
            "connectivity_reference": modes,
        }


def simulate(route_id):
    rng = Random(int(sha256(f"{SEED}:{route_id}".encode()), 16))
    level = rng.uniform(40, 160)
    trend = rng.uniform(-0.004, 0.009)
    amplitude = rng.uniform(0.02, 0.16)
    phase = rng.uniform(0, 2 * math.pi)
    shock_month = rng.randrange(18, 60)
    shock = rng.uniform(-0.18, 0.18)
    residual = 0.0
    for i in range(TRAIN_MONTHS + HOLDOUT_MONTHS):
        residual = 0.45 * residual + rng.gauss(0, 0.07)
        season = amplitude * math.sin(2 * math.pi * i / 12 + phase)
        shift = shock * 0.8 ** (i - shock_month) if i >= shock_month else 0
        tonnes = round(level * math.exp(trend * i + season + residual + shift), 1)
        yield {
            "route_id": route_id,
            "month": f"{FIRST_YEAR + i // 12}-{i % 12 + 1:02d}",
            "tonnes": tonnes,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connectivity", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source_bytes = args.connectivity.read_bytes()
    snapshot = json.loads(source_bytes)
    routes = list(select_routes(snapshot))
    train, holdout = [], []
    for route in routes:
        observations = list(simulate(route["route_id"]))
        train.extend(observations[:TRAIN_MONTHS])
        holdout.extend(observations[TRAIN_MONTHS:])

    args.output.mkdir(parents=True, exist_ok=False)

    def write(name, content):
        data = (json.dumps(content, ensure_ascii=False, indent=2) + "\n").encode()
        (args.output / name).write_bytes(data)
        return sha256(data)

    files = {
        "routes.json": write("routes.json", routes),
        "train.json": write("train.json", train),
        "holdout.json": write("holdout.json", holdout),
    }
    write("manifest.json", {
        "benchmark_version": 2,
        "volume_status": "synthetic",
        "volume_scope": "Один условный грузоотправитель на каждом направлении",
        "source_url": snapshot["source"]["url"],
        "source_snapshot_id": snapshot["id"],
        "source_snapshot_sha256": sha256(source_bytes),
        "generator_sha256": sha256(Path(__file__).read_bytes()),
        "seed": SEED,
        "routes": len(routes),
        "endpoint_regions": len({route[key] for route in routes for key in ("origin_region", "destination_region")}),
        "train_months_per_route": TRAIN_MONTHS,
        "holdout_months_per_route": HOLDOUT_MONTHS,
        "files_sha256": files,
        "protocol": [
            "География и связность взяты из исходного снимка. Грузовая доступность не подтверждена.",
            "Уровень, тренд, сезонность и возмущения объёма заданы синтетически, независимо от тарифов и схем.",
            "Модель и её параметры выбираются по train.json. Прогноз на 2025 год сохраняется до чтения holdout.json.",
            "После раскрытия holdout результаты считаются проверкой зафиксированной версии модели.",
        ],
    })
    print(f"{len(routes)} routes, {len(train)} training rows, {len(holdout)} held-out rows")


if __name__ == "__main__":
    main()
