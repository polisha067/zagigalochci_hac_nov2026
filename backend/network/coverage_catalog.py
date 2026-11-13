"""Read the compact settlement release without creating fictitious freight edges."""

import gzip
import json
import re
from collections import defaultdict
from functools import cache
from pathlib import Path

from backend.network.cargo import normalized_name


ROOT = Path(__file__).resolve().parents[1] / "data_sources/russia"
PROVIDER_LOCALITIES = {"Дудинка": "Красноярский край"}


@cache
def settlement_catalog():
    with gzip.open(ROOT / "gar_settlements.json.gz", "rt", encoding="utf-8") as stream:
        return json.load(stream)


def region_key(name):
    words = re.findall(r"[а-я]+", normalized_name(name or ""))
    return " ".join(sorted(word for word in words if word not in {"г", "город"}))


def merge_settlements(legacy, rows):
    """Retain every old ID and enrich it only on an unambiguous match."""
    candidates = defaultdict(list)
    for row in rows:
        candidates[normalized_name(row["name"])].append(row)
    matched = {}
    for identifier, city in legacy.items():
        group = candidates[normalized_name(city["name"])]
        regional = [row for row in group if region_key(row["region"]) == region_key(city.get("region"))]
        if city.get("region") and not regional:
            regional = [row for row in group if row.get("kind") == "г." and row.get("latitude") is not None
                        and city.get("latitude") is not None
                        and abs(row["latitude"] - city["latitude"]) < 0.08
                        and abs(row["longitude"] - city["longitude"]) < 0.08]
        if not city.get("region"):
            region = PROVIDER_LOCALITIES.get(city["name"])
            regional = [row for row in group if row["region"] == region] if region else group
        if len(regional) == 1:
            row = regional[0]
            matched[row["id"]] = identifier
            has_coordinates = city.get("latitude") is not None and city.get("longitude") is not None
            city.update({key: value for key, value in row.items() if city.get(key) is None
                         and not (has_coordinates and key == "coordinate_source")})
            city["gar_id"] = row["id"]
    legacy.update((row["id"], row) for row in rows if row["id"] not in matched)
    return matched
