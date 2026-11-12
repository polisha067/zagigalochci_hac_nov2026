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


@lru_cache(maxsize=16)
def read_snapshot(path):
    tariff = json.loads(path.read_text(encoding="utf-8"))
    if tariff["version"] != tariff_version(tariff):
        raise ValueError("Контрольная сумма тарифа не совпадает")
    if tariff["source_url"] != URL:
        raise ValueError("Неизвестный источник тарифа")
    date.fromisoformat(tariff["observed_on"])
    return tariff


def latest_snapshot(directory, cutoff=date.max):
    if directory is None:
        return None
    for path in sorted(directory.glob("tlm-*.json"), reverse=True):
        try:
            if date.fromisoformat(path.name[4:14]) > cutoff:
                continue
            tariff = read_snapshot(path)
            if tariff["valid_from"] != path.name[4:14]:
                raise ValueError("Дата тарифа не совпадает с именем файла")
            return path
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("Снимок ТЛМ %s пропущен: %s", path.name, exc)
    return None
