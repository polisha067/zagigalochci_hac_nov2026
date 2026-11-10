"""Published rates applied to matching freight services in the national model."""

import json
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "data_sources/delivery_forecast"
ROAD_SOURCE = "tlm-road-2026"
RAIL_SOURCE = "tlm-rail-2026"
SEA_SOURCE = "fesco-fpkl-tariff-2026"
SEA_EQUIPMENT_SOURCE = "fesco-20dc-equipment"
AIR_SOURCE = "yakutia-air-cargo-q4-2026"
FORECAST_BASE_SOURCES = frozenset({ROAD_SOURCE, RAIL_SOURCE})


@lru_cache(maxsize=1)
def observations():
    def read(name):
        return json.loads((ROOT / name).read_text(encoding="utf-8"))

    snapshot = read("provider_observations.json")
    records = {record["id"]: record for record in snapshot["records"]}
    documents = {doc["file"]: doc["url"] for doc in read("provider_documents.json")["documents"]}
    return snapshot["collected_on"], records, documents, read("tlm_road_2026.json")


def source(identifier, title, url, status="fact"):
    return {"id": identifier, "name": title, "url": url, "status": status,
            "roles": ["tariff"]}


def yakutia_tariffs():
    return json.loads((ROOT / "yakutia_air_2026.json").read_text(encoding="utf-8"))


def rate_evidence(source_id, observed_on, start, end, status="fact"):
    return {"source_ids": [source_id], "status": status, "observed_on": observed_on,
            "valid_from": start, "valid_until": end}
