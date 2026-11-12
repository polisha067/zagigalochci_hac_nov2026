"""Versioned snapshots of the TLM station-to-station container tariff."""

import hashlib
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from backend.network.provider_tariffs import observations


URL = "https://tl-m.ru/tariffs/container/"
ROAD_URL = "https://tl-m.ru/tariffs/magistral/"
MOSCOW = timezone(timedelta(hours=3))
RECORD = "tlm-moscow-container-rail"
logger = logging.getLogger(__name__)


def snapshot_dir():
    value = os.getenv("TLM_TARIFF_DIR")
    return Path(value) if value else None


def moscow_today():
    return datetime.now(MOSCOW).date()


def tariff_version(tariff):
    payload = [tariff["valid_from"], tariff["vat_percent"], tariff["rows"]]
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()[:16]


def bundled_tariff():
    collected_on, records, _documents, _road = observations()
    tariff = records[RECORD] | {"observed_on": collected_on}
    return tariff | {"version": tariff_version(tariff)}


def bundled_road_tariff():
    _collected_on, _records, _documents, road = observations()
    tariff = road | {"valid_from": f"{road['year']}-01-01",
                     "rows": sorted([city, rate] for city, rate in road["rub_per_vehicle"].items())}
    return tariff | {"version": tariff_version(tariff)}


@lru_cache(maxsize=16)
def read_snapshot(path):
    tariff = json.loads(path.read_text(encoding="utf-8"))
    if tariff["version"] != tariff_version(tariff):
        raise ValueError("Контрольная сумма тарифа не совпадает")
    expected_url = ROAD_URL if path.name.startswith("tlm-road-") else URL
    if tariff["source_url"] != expected_url:
        raise ValueError("Неизвестный источник тарифа")
    date.fromisoformat(tariff["observed_on"])
    return tariff


def latest_snapshot(directory, cutoff=date.max, *, road=False):
    if directory is None:
        return None
    prefix = "tlm-road-" if road else "tlm-"
    for path in sorted(directory.glob(f"{prefix}[0-9]*.json"), reverse=True):
        try:
            filename_date = path.name[len(prefix):len(prefix) + 10]
            if date.fromisoformat(filename_date) > cutoff:
                continue
            tariff = read_snapshot(path)
            applicable_from = max(tariff["valid_from"], tariff["observed_on"]) if road else tariff["valid_from"]
            if applicable_from != filename_date:
                raise ValueError("Дата тарифа не совпадает с именем файла")
            return path
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("Снимок ТЛМ %s пропущен: %s", path.name, exc)
    return None
