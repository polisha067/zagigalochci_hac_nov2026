"""Нормализация открытых данных. Наблюдение не превращается в грузовую услугу."""

from contextlib import contextmanager
from datetime import datetime, timezone
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import sqlite3
import zipfile


MISSING = {"", "CD", "ND", "NA", "N/A"}


def value(text):
    text = str(text or "").strip()
    return None if text in MISSING else text


def numeric(text, nonnegative=False):
    text = value(text)
    if text is None:
        return None
    result = float(text.replace(",", "."))
    if not math.isfinite(result) or (nonnegative and result < 0):
        raise ValueError("Некорректный числовой показатель в исходном файле")
    return result


@contextmanager
def csv_rows(path):
    path = Path(path)
    if path.stat().st_size > 100_000_000:
        raise ValueError("Файл больше технического предела 100 MB")
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            entries = [item for item in archive.infolist() if item.filename.lower().endswith(".csv") and not item.filename.startswith("__MACOSX/") and not Path(item.filename).name.startswith("._")]
            if len(entries) != 1 or entries[0].file_size > 100_000_000:
                raise ValueError("Ожидается один CSV до 100 MB; извлечение путей архива не выполняется")
            with archive.open(entries[0]) as stream:
                yield csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig"), delimiter=";")
    else:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            yield csv.DictReader(stream, delimiter=";")


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS observation_sources (id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS city_observations (
            source_id TEXT NOT NULL REFERENCES observation_sources(id),
            oktmo TEXT NOT NULL, name TEXT NOT NULL, region TEXT,
            latitude REAL, longitude REAL,
            PRIMARY KEY(source_id,oktmo)
        );
        CREATE TABLE IF NOT EXISTS connectivity_observations (
            source_id TEXT NOT NULL REFERENCES observation_sources(id),
            from_oktmo TEXT NOT NULL, to_oktmo TEXT NOT NULL,
            mode TEXT NOT NULL, distance_km REAL, estimated_hours REAL,
            missing_reason TEXT, raw_source TEXT, comment TEXT,
            PRIMARY KEY(source_id,from_oktmo,to_oktmo,mode)
        );
        CREATE TABLE IF NOT EXISTS regional_observations (
            source_id TEXT NOT NULL REFERENCES observation_sources(id),
            row_number INTEGER NOT NULL, indicator_code TEXT NOT NULL,
            indicator_name TEXT NOT NULL, object_name TEXT NOT NULL,
            object_level TEXT, object_oktmo TEXT, year INTEGER NOT NULL,
            value REAL, unit TEXT NOT NULL, comment TEXT, raw_source TEXT,
            PRIMARY KEY(source_id,row_number)
        );
        CREATE INDEX IF NOT EXISTS regional_indicator ON regional_observations(indicator_code,object_oktmo,year);
    """)
    return connection


def import_observations(input_path, database_path, kind):
    source_id = "source-01" if kind == "connectivity" else "source-02"
    if Path(input_path).stat().st_size > 100_000_000:
        raise ValueError("Файл больше технического предела 100 MB")
    hasher = hashlib.sha256()
    with Path(input_path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    file_hash = hasher.hexdigest()
    metadata = {"id":source_id,"file_sha256":file_hash,"kind":kind,"imported_date":datetime.now(timezone.utc).date().isoformat(),"freight_services_created":0,"price_data_created":0}
    connection = connect(database_path)
    count, modes, indicators, missing_units = 0, {}, set(), 0
    try:
        with connection:
            connection.execute("INSERT INTO observation_sources VALUES (?,?) ON CONFLICT(id) DO UPDATE SET metadata_json=excluded.metadata_json", (source_id,json.dumps(metadata,ensure_ascii=False)))
            if kind == "connectivity":
                connection.execute("DELETE FROM connectivity_observations WHERE source_id=?", (source_id,))
                connection.execute("DELETE FROM city_observations WHERE source_id=?", (source_id,))
            elif kind == "regional":
                connection.execute("DELETE FROM regional_observations WHERE source_id=?", (source_id,))
            else:
                raise ValueError("Неизвестный вид импорта")
            with csv_rows(input_path) as rows:
                expected = {"city_from","city_to","oktmo_from","oktmo_to","transport_type","distance_km","distance_hr","missing_reason"} if kind=="connectivity" else {"indicator_code","indicator_name","object_name","year","indicator_value","indicator_unit"}
                if not expected.issubset(set(rows.fieldnames or [])):
                    raise ValueError("Схема CSV не совпадает с ожидаемой; автоматическая догадка запрещена")
                for count,row in enumerate(rows,1):
                    if count > 200000:
                        raise ValueError("Больше 200000 строк за импорт")
                    if kind == "connectivity":
                        for suffix in ("from","to"):
                            identifier, name = value(row.get("oktmo_"+suffix)), value(row.get("city_"+suffix))
                            if identifier is None or name is None:
                                raise ValueError("Нет имени/ОКТМО города")
                            lat,lon=numeric(row.get("lat_"+suffix)),numeric(row.get("lon_"+suffix))
                            if (lat is not None and not -90<=lat<=90) or (lon is not None and not -180<=lon<=180):
                                raise ValueError("Некорректные координаты")
                            city=(source_id,identifier,name,value(row.get("region_"+suffix)),lat,lon)
                            existing=connection.execute("SELECT name,region,latitude,longitude FROM city_observations WHERE source_id=? AND oktmo=?",(source_id,identifier)).fetchone()
                            if existing is not None and existing != city[2:]:
                                raise ValueError("Противоречивые свойства города по одному ОКТМО")
                            connection.execute("INSERT OR IGNORE INTO city_observations VALUES (?,?,?,?,?,?)",city)
                        mode=row["transport_type"]
                        if mode not in ("road","rail","air"):
                            raise ValueError("Неизвестная транспортная категория исходного CSV: "+mode)
                        distance,hours=numeric(row["distance_km"],True),numeric(row["distance_hr"],True)
                        if (distance is None)!=(hours is None):
                            raise ValueError("Расстояние и время имеют неодинаковую полноту")
                        connection.execute("INSERT INTO connectivity_observations VALUES (?,?,?,?,?,?,?,?,?)",(source_id,value(row["oktmo_from"]),value(row["oktmo_to"]),mode,distance,hours,value(row.get("missing_reason")),value(row.get("source")),value(row.get("comment"))))
                        modes[mode]=modes.get(mode,0)+1
                    else:
                        unit=value(row.get("indicator_unit"))
                        if not unit:
                            # Сохраняем исходную строку, но не придумываем единицу и не используем как признак.
                            unit="unknown"
                            missing_units+=1
                        year=int(row["year"])
                        if not 1900<=year<=2100:
                            raise ValueError("Неожиданный год")
                        connection.execute("INSERT INTO regional_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",(source_id,count,row["indicator_code"],row["indicator_name"],row["object_name"],value(row.get("object_level")),value(row.get("object_oktmo")),year,numeric(row["indicator_value"]),unit,value(row.get("comment")),value(row.get("source"))))
                        indicators.add(row["indicator_code"])
            metadata.update(rows=count,modes=modes,indicators=len(indicators),missing_units=missing_units)
            if kind == "connectivity":
                metadata["cities"]=connection.execute("SELECT count(*) FROM city_observations WHERE source_id=?",(source_id,)).fetchone()[0]
                metadata["missing_routes"]=connection.execute("SELECT count(*) FROM connectivity_observations WHERE source_id=? AND distance_km IS NULL",(source_id,)).fetchone()[0]
            connection.execute("UPDATE observation_sources SET metadata_json=? WHERE id=?",(json.dumps(metadata,ensure_ascii=False),source_id))
    finally:
        connection.close()
    return metadata


def coverage(database_path):
    path=Path(database_path)
    if not path.exists():
        return {"sources":[],"note":"Наблюдения ещё не импортированы; это не означает отсутствия транспорта"}
    connection=sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True)
    try:
        records=[json.loads(row[0]) for row in connection.execute("SELECT metadata_json FROM observation_sources ORDER BY id")]
    finally:
        connection.close()
    return {"sources":records,"note":"Наблюдения о городах и статистика отделены от подтверждённых грузовых услуг и хабов"}
