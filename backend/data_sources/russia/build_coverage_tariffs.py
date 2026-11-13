"""Extract the carrier's directed rate rows from saved public HTML."""

import argparse
import hashlib
import json
import re
from pathlib import Path

from lxml.html import fromstring


ROOT = Path(__file__).resolve().parent
ROAD_SUFFIX = "/autopark/gruzoperevozki-do-20-tonn/"
SIBIR_URL = "https://www.tk-container.com/price"
SIBIR_EQUIPMENT = "https://www.tk-container.com/tiporazmery-kontejnerov"
TRANSIT_URL = "https://transitllc.ru/tariffs"
UNCONFIRMED_RAIL = {"Калининград", "Магадан", "Петропавловск-Камчатский", "Южно-Сахалинск", "Якутск", "Лена-перевалка"}


def matrix(tree):
    for table in tree.xpath("//table"):
        for row in table.xpath(".//tr"):
            cells = row.xpath("./th|./td")
            yield [" ".join(cell.text_content().replace("\u00ad", "").split()) for cell in cells]


def number(value):
    return int(re.sub(r"\s+", "", value))


def road(rows):
    for row in rows:
        if len(row) != 3 or " - " not in row[0]:
            continue
        origin, destination = row[0].split(" - ", 1)
        if origin == destination:
            continue
        distance = re.fullmatch(r"([\d ]+) км", row[1])
        rate = re.fullmatch(r"([\d ]+) р\.?", row[2])
        if distance and rate:
            yield {"origin": origin, "destination": destination, "mode": "road",
                   "equipment": "20-тонный автомобиль", "unit": "vehicle", "max_tonnes": 20,
                   "max_m3": None, "amount_rub": number(rate[1]), "vat": "unknown",
                   "distance_km": number(distance[1]), "distance_kind": "carrier_reference",
                   "scope": "road_linehaul"}


def sibir(rows):
    for row in rows:
        if len(row) != 4 or " – " not in row[0]:
            continue
        origin, destination = row[0].split(" – ", 1)
        days = [int(n) for n in re.findall(r"\d+", row[3])]
        for size, value in zip((20, 40), row[1:3]):
            if not re.fullmatch(r"[\d ]+", value):
                continue
            yield {"origin": origin, "destination": destination,
                   "mode": None if {origin, destination} & UNCONFIRMED_RAIL else "rail",
                   "equipment": f"Контейнер {size} футов", "unit": "container",
                   "max_tonnes": 28.2 if size == 20 else 26.58,
                   "max_m3": 33.1 if size == 20 else 67.5,
                   "equipment_source_url": SIBIR_EQUIPMENT,
                   "internal_dimensions_m": [5.758, 2.352, 2.385] if size == 20 else [12, 2.311, 2.385],
                   "door_dimensions_m": [2.343, 2.28] if size == 20 else [2.28, 2.28],
                   "amount_rub": number(value), "vat": "included",
                   "transit_days": days, "scope": "container_linehaul"}


def transit(rows):
    for row in rows:
        if len(row) != 4 or not row[2].startswith("от ") or not row[3].startswith("от "):
            continue
        yield {"origin": row[0], "destination": row[1], "mode": None, "scope": "lcl_terminal_to_terminal",
               "from_rub_per_m3": number(row[2].removeprefix("от ").removesuffix(" ₽")),
               "from_rub_per_kg": float(row[3].removeprefix("от ").removesuffix(" ₽").replace(",", ".")),
               "vat": "included"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--observed-on", required=True)
    args = parser.parse_args()
    files = []
    for path in sorted(args.input.glob("road-*.html")):
        city = path.stem.removeprefix("road-")
        domain = "logistic-avto.ru" if city == "moscow" else f"{city}.logistic-avto.ru"
        files.append((path, f"logistic-avto-regional-{city}", "Логистик Авто — прайс на перевозки 20 т",
                      f"https://{domain}{ROAD_SUFFIX}", road))
    files += [
        (args.input / "sibir.html", "sibir-container-expanded", "Сибирь Контейнер — прайс по направлениям",
         SIBIR_URL, sibir),
        (args.input / "transit.html", "transit-lcl-reference", "Транзит — сборные грузы, цены от",
         TRANSIT_URL, transit),
    ]
    sources, tariffs, rejected = [], [], []
    for path, identifier, title, url, parse in files:
        if not path.is_file():
            continue
        data = path.read_bytes()
        rows = list(parse(matrix(fromstring(data.decode("utf-8")))))
        if not rows:
            rejected.append({"source": identifier, "reason": "no_matching_published_table"})
            continue
        sources.append({"id": identifier, "name": title, "url": url, "status": "market_reference",
                        "roles": ["tariff"], "observed_on": args.observed_on, "sha256": hashlib.sha256(data).hexdigest()})
        if identifier == "sibir-container-expanded":
            sources[-1]["separate_services"] = [
                {"name": "Автодоставка контейнера на склад", "from_rub": 4500, "unit": "service"},
                {"name": "Погрузка или выгрузка на площадке", "from_rub": 3000, "unit": "service"},
                {"name": "Хранение гружёного контейнера", "from_rub": 600, "unit": "day"},
            ]
        seen = set()
        for row in rows:
            key = json.dumps(row, sort_keys=True, ensure_ascii=False)
            if key in seen:
                rejected.append({"source": identifier, "reason": "duplicate", "origin": row["origin"],
                                 "destination": row["destination"]})
                continue
            seen.add(key)
            tariffs.append(row | {"source_id": identifier})
    if not tariffs:
        parser.error("No tariffs parsed")
    target = ROOT / "coverage_tariffs.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"sources": sources, "tariffs": tariffs, "rejected": rejected},
                                    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    print(f"{len(sources)} sources, {len(tariffs)} rates, {len(rejected)} rejected")


if __name__ == "__main__":
    main()
