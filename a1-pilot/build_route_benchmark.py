"""Freeze a synthetic multi-route benchmark before fitting any forecast."""
import argparse
import hashlib
import json
import math
from pathlib import Path
from random import Random

DISTANCE_BANDS_KM = ((200, 700), (700, 1500), (1500, 4000))
ROUTES_PER_BAND = 4
SEED = 826041
FIRST_YEAR = 2021
TRAIN_MONTHS = 48
HOLDOUT_MONTHS = 12


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def select_routes(snapshot, rng):
    cities = {city["id"]: city for city in snapshot["settlements"]}
    links = {}
    for origin, destination, mode, km, hours, reason in snapshot["connections"]:
        if mode in ("road", "rail") and not reason and km and hours and km > 0 and hours > 0:
            links.setdefault((origin, destination), {})[mode] = {"km": km, "hours": hours}
    eligible = [
        (pair, modes) for pair, modes in sorted(links.items())
        if {"road", "rail"} <= modes.keys()
        and {cities[key]["name"] for key in pair} != {"Москва", "Кемерово"}
    ]
    used_pairs = set()
    for low, high in DISTANCE_BANDS_KM:
        candidates = [
            (pair, modes) for pair, modes in eligible
            if low <= modes["road"]["km"] < high and frozenset(pair) not in used_pairs
        ]
        rng.shuffle(candidates)
        selected = 0
        for (origin, destination), modes in candidates:
            pair = frozenset((origin, destination))
            if pair in used_pairs:
                continue
            used_pairs.add(pair)
            yield {
                "route_id": f"{origin}-{destination}",
                "origin": cities[origin]["name"],
                "destination": cities[destination]["name"],
                "distance_band_km": [low, high],
                "connectivity_reference": modes,
            }
            selected += 1
            if selected == ROUTES_PER_BAND:
                break
        if selected != ROUTES_PER_BAND:
            raise ValueError(f"Too few eligible directions in distance band {low}–{high} km")


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
    routes = list(select_routes(snapshot, Random(SEED)))
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
        "volume_status": "synthetic",
        "volume_scope": "Один условный грузоотправитель на каждом направлении",
        "source_url": snapshot["source"]["url"],
        "source_snapshot_id": snapshot["id"],
        "source_snapshot_sha256": sha256(source_bytes),
        "generator_sha256": sha256(Path(__file__).read_bytes()),
        "seed": SEED,
        "routes": len(routes),
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
