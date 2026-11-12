"""Fetch and publish compatible updates to the TLM container tariff."""

import argparse
import json
import logging
import os
import re
import tempfile
import time
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

from backend.network.tlm_tariff import (
    MOSCOW, URL, bundled_tariff, latest_snapshot, read_snapshot, snapshot_dir, tariff_version,
)


logger = logging.getLogger(__name__)
MAX_BYTES = 2_000_000
HEADER = ["Пункт назначения", "Тип", "Стоимость (руб)", "Охрана (руб)", "Срок (суток)"]
INCLUDED = {
    "ЖД тариф и предоставление платформы.",
    "Подача контейнера под погрузку.",
    "Погрузо-разгрузочные работы (терминал).",
    "Дислокация и отслеживание.",
    "ЗПУ (пломбы).",
}
EXTRAS = {
    "Простой на погрузке: свыше 3ч (20фт) / 4ч (40фт) — 1000 руб/час.",
    "Сверхнорматив: пользование контейнером свыше срока — 1000 руб/сутки.",
    "МКАД: выезд за пределы — 80 руб/км.",
}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def normalized(value):
    return " ".join(value.split())


class TariffPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.blocks = []
        self.block = None
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag in HEADINGS or tag == "li":
            self.block = (tag, [])
        if tag == "tr":
            self.row = []
        if tag in {"td", "th"} and self.row is not None:
            self.cell = []
        if tag == "br":
            self.handle_data(" ")

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)
        if self.block is not None:
            self.block[1].append(data)

    def handle_endtag(self, tag):
        if self.block is not None and self.block[0] == tag:
            self.blocks.append((tag, normalized("".join(self.block[1]))))
            self.block = None
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append(normalized("".join(self.cell)))
            self.cell = None
        if tag == "tr" and self.row is not None:
            self.blocks.append((tag, self.row))
            self.row = None
        if tag == "table":
            self.blocks.append((tag, None))


def contract_items(blocks, heading):
    matches = [i for i, (tag, value) in enumerate(blocks) if tag in HEADINGS and value == heading]
    if len(matches) != 1:
        raise ValueError(f"В прайсе ТЛМ изменился раздел «{heading}»")
    start = matches[0]
    items = []
    for tag, value in blocks[start + 1:]:
        if tag != "li":
            break
        items.append(value)
    return set(items)


def rubles(value):
    digits = value.replace(" ", "")
    if not re.fullmatch(r"[0-9]{1,9}", digits):
        raise ValueError("Некорректная сумма в таблице ТЛМ")
    return int(digits)


def parse_page(html, previous):
    page = TariffPage()
    page.feed(html)
    page.close()
    dates = [match[1] for tag, value in page.blocks if tag in HEADINGS
             and (match := re.fullmatch(r"Тарифы с (\d{2}\.\d{2}\.\d{4}) \(От станции до станции\)", value, re.I))]
    if len(dates) != 1:
        raise ValueError("Не найдены дата или условия тарифа от станции до станции")
    effective = datetime.strptime(dates[0], "%d.%m.%Y").date()
    if contract_items(page.blocks, "В стоимость включено (НДС 22%):") != INCLUDED:
        raise ValueError("Изменились НДС или состав включённых услуг ТЛМ")
    if contract_items(page.blocks, "Важная информация:") != EXTRAS:
        raise ValueError("Изменились дополнительные условия тарифа ТЛМ")
    headers = [i for i, (tag, value) in enumerate(page.blocks) if tag == "tr" and value == HEADER]
    if len(headers) != 1:
        raise ValueError("Изменились колонки тарифной таблицы ТЛМ")
    start = headers[0]
    rates = {}
    destination = None
    for tag, cells in page.blocks[start + 1:]:
        if tag != "tr":
            break
        if len(cells) == 5:
            destination, size, price, security, days = cells
        elif destination and len(cells) in (3, 4):
            size, price, security, *rest = cells
            days = rest[0] if rest and rest[0] else rates.get((destination, 20), (None, None, ""))[2]
        else:
            raise ValueError("Изменилась структура тарифной таблицы ТЛМ")
        size_match = re.fullmatch(r"(20|40)[-–‑ ]фут\.?", size, re.I)
        if not destination or not size_match or not re.fullmatch(r"[0-9]+(?:[.,][0-9]+)?\+?", days):
            raise ValueError("Некорректный тип контейнера или срок в прайсе ТЛМ")
        key = (destination, int(size_match[1]))
        price, security = rubles(price), rubles(security)
        if key in rates or price == 0 or float(days.rstrip("+").replace(",", ".")) <= 0:
            raise ValueError("Повтор или нулевой тариф либо срок ТЛМ")
        rates[key] = (price, security, days.replace(",", "."))

    previous_rows = {row[0]: row for row in previous["rows"]}
    cities = set(city for city, _size in rates)
    if not previous_rows.keys() <= cities:
        raise ValueError("В прайсе ТЛМ пропали ранее опубликованные направления")
    rows = []
    for city in [*previous_rows, *sorted(cities - previous_rows.keys())]:
        if (city, 20) not in rates or (city, 40) not in rates:
            raise ValueError(f"В прайсе ТЛМ не хватает одного из типоразмеров для {city}")
        price20, security20, days20 = rates[city, 20]
        price40, security40, days40 = rates[city, 40]
        if days20 != days40:
            raise ValueError(f"У контейнеров разные сроки для {city}")
        if city in previous_rows and days20.endswith("+") != previous_rows[city][-1].endswith("+"):
            raise ValueError(f"Изменились условия следования до {city}")
        rows.append([city, price20, price40, security20, security40, days20])
    return {"valid_from": effective.isoformat(), "vat_percent": 22, "rows": rows}


def fetch_page():
    request = Request(URL, headers={"User-Agent": "Poputno tariff updater/1.0"})
    with urlopen(request, timeout=20) as response:
        if response.geturl().split("/", 3)[2] != "tl-m.ru":
            raise ValueError("Прайс перенаправлен на другой домен")
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("Ответ ТЛМ превышает допустимый размер")
        return data.decode(response.headers.get_content_charset() or "utf-8")


def refresh(directory, *, now=None):
    checked = now or datetime.now(timezone.utc)
    previous_path = latest_snapshot(directory)
    previous = read_snapshot(previous_path) if previous_path else bundled_tariff()
    tariff = parse_page(fetch_page(), previous)
    if date.fromisoformat(tariff["valid_from"]) < date.fromisoformat(previous["valid_from"]):
        raise ValueError("На странице ТЛМ появилась более старая дата тарифа")
    version = tariff_version(tariff)
    if previous_path and version == previous["version"]:
        return None
    tariff.update(version=version, source_url=URL, observed_on=checked.astimezone(MOSCOW).date().isoformat())
    directory.mkdir(parents=True, exist_ok=True)
    stamp = checked.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    path = directory / f"tlm-{tariff['valid_from']}-{stamp}-{version}.json"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False, mode="w", encoding="utf-8") as file:
            temporary = Path(file.name)
            json.dump(tariff, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=snapshot_dir())
    parser.add_argument("--interval-seconds", type=int, default=0)
    args = parser.parse_args()
    if args.directory is None or args.interval_seconds < 0:
        parser.error("Укажите TLM_TARIFF_DIR или --directory и неотрицательный интервал")
    while True:
        try:
            result = refresh(args.directory)
            logger.info("Прайс ТЛМ: %s", result.name if result else "изменений нет")
            status = 0
        except (OSError, ValueError, LookupError) as exc:
            logger.error("Прайс ТЛМ не обновлён: %s", exc)
            status = 1
        if not args.interval_seconds:
            return status
        time.sleep(min(args.interval_seconds, 3600) if status else args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
