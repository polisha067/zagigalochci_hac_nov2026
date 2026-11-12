"""Archived releases of the Yakutia domestic air freight tariff."""

import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import urlsplit

from backend.network.provider_tariffs import observations, yakutia_tariffs

PAGE_URL = "https://www.yakutia.aero/uslugi/gruzovye-perevozki-na-vnutrennikh-reisakh/"
YAKUTSK = timezone(timedelta(hours=9))
logger = logging.getLogger(__name__)


def yakutsk_today():
    return datetime.now(YAKUTSK).date()


def is_publisher_url(url):
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.netloc == "www.yakutia.aero"


def version_of(tariff):
    fields = ("valid_from", "valid_until", "vat", "breakpoints_kg", "minimum_weight_kg",
              "actual_weight_step_kg", "cubic_cm_per_kg", "awb_rub", "federal_rows")
    content = json.dumps([tariff[key] for key in fields], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(content.encode()).hexdigest()[:16]


@lru_cache(maxsize=1)
def bundled_air_tariff():
    _, _, documents, _ = observations()
    tariff = yakutia_tariffs()
    return tariff | {"source_url": documents[tariff["source"]], "version": version_of(tariff)}


@lru_cache(maxsize=16)
def read_air_snapshot(path):
    tariff = json.loads(path.read_text(encoding="utf-8"))
    if (tariff["version"] != version_of(tariff) or not is_publisher_url(tariff["source_url"])
            or date.fromisoformat(tariff["valid_from"]) >= date.fromisoformat(tariff["valid_until"])):
        raise ValueError("Некорректный снимок авиатарифа")
    date.fromisoformat(tariff["observed_on"])
    return tariff


def latest_air_snapshot(directory, cutoff=date.max):
    if directory is None:
        return None
    for path in sorted(directory.glob("yakutia-*.json"), reverse=True):
        try:
            effective = path.name[8:18]
            if date.fromisoformat(effective) <= cutoff:
                if read_air_snapshot(path)["valid_from"] != effective:
                    raise ValueError("Дата тарифа не совпадает с именем файла")
                return path
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("Снимок Якутии %s пропущен: %s", path.name, exc)
    return None
