"""Build the locality snapshot from regional GAR XML and the census crosswalk."""

import argparse
import csv
import gzip
import hashlib
import io
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from xml.etree.ElementTree import iterparse
from zipfile import ZipFile

from backend.network.cargo import normalized_name


HERE = Path(__file__).resolve().parent
CENSUS = {
    "id": "tochno-census-2021-v20260925",
    "name": "ВПН-2020, срез на 01.10.2021, нормализация «Если быть точным»",
    "url": "https://tochno.st/datasets/allsettlements",
    "roles": ["population", "settlement_crosswalk"],
}
FEDERAL_CITIES = {"77", "78", "92"}
GAR = {
    "id": "gar-2026-11-10",
    "name": "ФНС России — Государственный адресный реестр",
    "url": "https://www.nalog.gov.ru/opendata/7707329152-fias/",
    "archive": "https://fias.nalog.ru/opendata/7707329152-fias/data-10112026-structure-20191024.zip",
    "roles": ["settlement_registry"],
}


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def census_rows(path):
    with ZipFile(path) as archive:
        member, = (name for name in archive.namelist() if name.endswith(".csv") and "/" not in name)
        with archive.open(member) as stream:
            for row in csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig"), delimiter=";"):
                if row["object_level"] in {"Населенный пункт", "Населённый пункт"}:
                    yield row
                elif row["object_level"] == "Регион" and row["region"] in {"Москва", "Санкт-Петербург", "Севастополь"}:
                    yield row | {"settlement": row["region"]}


def read_region(path):
    context = iterparse(path, events=("start", "end"))
    _, root = next(context)
    code = path.stem.removeprefix("gar-")
    region = None
    localities = []
    for event, node in context:
        if event != "end" or node is root:
            continue
        row = node.attrib
        if row.get("ISACTIVE") == "1" and row.get("ISACTUAL") == "1":
            level = row.get("LEVEL")
            if level == "1":
                kind = row["TYPENAME"]
                region = f"Республика {row['NAME']}" if kind == "респ." else row["NAME"]
                suffix = {"обл.": "область", "край": "край", "а.окр.": "автономный округ"}.get(kind)
                if suffix and suffix not in region.casefold():
                    region = f"{region} {suffix}"
            if level in {"5", "6"} or level == "2" and row["TYPENAME"] == "г." or level == "1" and code in FEDERAL_CITIES:
                localities.append({
                    "id": "gar:" + row["OBJECTGUID"], "name": row["NAME"],
                    "kind": row["TYPENAME"], "region_code": code, "provenance": GAR["id"],
                })
        root.clear()
    return region, localities


def region_key(value):
    name = normalized_name(value)
    name = name.removeprefix("город ").removeprefix("г ")
    return name.replace("чувашия", "").replace("югра", "").replace("алания", "").replace("кузбасс", "").strip(" -")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Directory containing gar-NN.xml and tochno.zip")
    args = parser.parse_args()
    files = sorted(args.input.glob("gar-*.xml"))
    if not files:
        parser.error("No GAR region files")
    localities = {}
    sources = []
    for path in files:
        region, rows = read_region(path)
        code = path.stem.removeprefix("gar-")
        if not region or not rows:
            sources.append({"region_code": code, "sha256": checksum(path), "records": 0})
            continue
        sources.append({"region_code": code, "sha256": checksum(path), "records": len(rows)})
        for row in rows:
            localities.setdefault(row["id"], row | {"region": region})

    by_name = defaultdict(list)
    for row in localities.values():
        by_name[normalized_name(row["name"]), region_key(row["region"])].append(row)
    census_path = args.input / "tochno.zip"
    matched = Counter()
    required = []
    if census_path.is_file():
        for row in census_rows(census_path):
            original = row["settlement"]
            city = re.match(r"^(?:г\.|город)\s*", original) is not None
            name = re.sub(r"^(?:г\.|город)\s*", "", original) if city else (row["settlement_dadata"] or original)
            if not name:
                continue
            population = row["population"]
            identifier = "gar:" + (row["settlement_fias_id_dadata"] or row["fias_id_dadata"])
            locality = localities.get(identifier)
            if locality is not None and normalized_name(locality["name"]) != normalized_name(name):
                locality = None
            match = "gar_id"
            if locality is None:
                candidates = by_name[normalized_name(name), region_key(row["region"])]
                if city:
                    candidates = [item for item in candidates if item["kind"] == "г."]
                locality = candidates[0] if len(candidates) == 1 else None
                match = "unique_name_region"
            if locality is not None:
                matched[match] += 1
                district = row["mun_lower"] or row["mun_upper"]
                if district:
                    locality.setdefault("district", district)
                if population.isdecimal():
                    locality.setdefault("population", int(population))
                matching_geocode = normalized_name(row["settlement_dadata"]) == normalized_name(name)
                if matching_geocode and row["qc_geo_dadata"] in {"0", "1", "2", "3", "4"} and row["latitude"] and row["longitude"]:
                    lat, lon = float(row["latitude"]), float(row["longitude"])
                    if -90 <= lat <= 90 and -180 <= lon <= 180:
                        locality.setdefault("latitude", lat)
                        locality.setdefault("longitude", lon)
                        locality["coordinate_source"] = CENSUS["id"]
            if population.isdecimal() and int(population) >= 50000:
                required.append({"name": name, "region": row["region"], "population": int(population),
                                 "gar_id": locality["id"] if locality else None})
        CENSUS["sha256"] = checksum(census_path)

    centre_reference = json.loads((HERE / "coverage_centres.json").read_text(encoding="utf-8"))
    centres = []
    locality_by_code_name = defaultdict(list)
    for locality in localities.values():
        locality_by_code_name[locality["region_code"], normalized_name(locality["name"])].append(locality)
    for district, regions in centre_reference["districts"].items():
        for code, name in regions.items():
            group = locality_by_code_name[code, normalized_name(name)]
            cities = [row for row in group if row["kind"] == "г."]
            matches = cities or group
            centres.append({"district": district, "region_code": code, "name": name,
                            "gar_id": matches[0]["id"] if len(matches) == 1 else None})
    payload = {"source": GAR, "census": CENSUS if census_path.is_file() else None, "regions": sources,
               "regional_centres": centres, "centre_reference": centre_reference["reference"],
               "required_population_50000": required, "census_matches": dict(matched),
               "settlements": sorted(localities.values(), key=lambda row: row["id"])}
    temporary = HERE / "gar_settlements.json.gz.tmp"
    temporary.write_bytes(gzip.compress(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0))
    temporary.replace(HERE / "gar_settlements.json.gz")
    print(f"GAR: {len(localities)} localities, {sum(bool(r['records']) for r in sources)} region files. "
          f"Census: {len(required)} localities >= 50000, {sum(not r['gar_id'] for r in required)} unmatched.")


if __name__ == "__main__":
    main()
