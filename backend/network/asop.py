"""Reviewed ASOP chart labels: aggregate observations, never freight services."""
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from backend.network.observations import connect

DATASET_ID = "asop-reviewed-pdfs-v1"
SNAPSHOT = Path(__file__).resolve().parents[1] / "data_sources/asop/observations.json"
METRICS = {"port_inbound", "port_outbound", "basin", "direction", "dry_cargo", "liquid_cargo"}


def load_snapshot(path=SNAPSHOT):
    path = Path(path)
    if path.stat().st_size > 10_000_000:
        raise ValueError("ASOP snapshot exceeds 10 MB")
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    if snapshot.get("schema_version") != "1.0" or snapshot.get("dataset_id") != DATASET_ID:
        raise ValueError("Unknown reviewed ASOP snapshot version")
    sources = snapshot["source_files"]
    ids = {source["id"] for source in sources}
    if not sources or len(ids) != len(sources):
        raise ValueError("Duplicate or missing source files")
    for source in sources:
        sha = source["sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", sha) or source["id"] != "asop-" + sha[:16]:
            raise ValueError("Invalid PDF fingerprint")
        if source["pages"] != 1:
            raise ValueError("This reviewed snapshot expects one-page PDF sources")
    rows = snapshot["observations"]
    if not 1 <= len(rows) <= 100000 or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate observations or invalid row count")
    for row in rows:
        if row["source_file_id"] not in ids or row["metric"] not in METRICS:
            raise ValueError("Unknown PDF source or metric")
        if row["period_kind"] not in ("year", "Q1") or type(row["year"]) is not int or not 1900 <= row["year"] <= 2100:
            raise ValueError("Invalid period")
        year = row["year"]
        end = date(year + 1, 1, 1) if row["period_kind"] == "year" else date(year, 4, 1)
        if row["period_start"] != date(year, 1, 1).isoformat() or row["period_end_exclusive"] != end.isoformat():
            raise ValueError("Inconsistent period boundaries")
        literal = row["source_value"]
        if not isinstance(literal, str) or not re.fullmatch(r"\d+\.\d{1,6}", literal):
            raise ValueError("Preserve finite decimal chart labels as strings")
        number = Decimal(literal)
        places = -number.as_tuple().exponent
        if row["source_decimal_places"] != places or Decimal(row["rounding_half_step_source"]) != Decimal(1).scaleb(-places) / 2:
            raise ValueError("Invalid source precision")
        unit = row["source_unit"]
        if unit == "million_tonnes":
            if type(row["value_tonnes"]) is not int or Decimal(row["value_tonnes"]) != number * 1000000:
                raise ValueError("Invalid conversion from million tonnes")
        elif unit == "unknown":
            if row["value_tonnes"] is not None or "unknown_unit" not in row["quality_flags"]:
                raise ValueError("Unknown units must never be converted")
        else:
            raise ValueError("Unexpected source unit")
        if row["reference_role"] not in ("current", "comparison") or row["page"] != 1:
            raise ValueError("Invalid provenance")
        expected = f"{row['source_file_id']}:{row['metric']}:{year}:{row['category_id']}"
        if row["id"] != expected or row["is_aggregate"] is not True:
            raise ValueError("Inconsistent observation identity")
    return snapshot


def verify_pdfs(snapshot, directory):
    """Optional audit against original filenames supplied by the user."""
    for source in snapshot["source_files"]:
        path = Path(directory) / source["filename"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError("PDF fingerprint mismatch: " + source["filename"])
    return len(snapshot["source_files"])


def import_asop(path, database_path):
    snapshot = load_snapshot(path)
    metadata = {
        "id": DATASET_ID, "kind": "asop_reviewed_charts",
        "file_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "reviewed_on": snapshot["reviewed_on"], "files": len(snapshot["source_files"]),
        "rows": len(snapshot["observations"]), "issues": len(snapshot["issues"]),
        "flagged_rows": sum(bool(row["quality_flags"]) for row in snapshot["observations"]),
        "periods": sorted({row["period_kind"] for row in snapshot["observations"]}),
        "annual_years": sorted({row["year"] for row in snapshot["observations"] if row["period_kind"] == "year"}),
        "quarterly_years": sorted({row["year"] for row in snapshot["observations"] if row["period_kind"] == "Q1"}),
        "freight_services_created": 0, "price_data_created": 0,
    }
    connection = connect(database_path)
    try:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS asop_files (
                id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS asop_observations (
                id TEXT PRIMARY KEY, source_file_id TEXT NOT NULL REFERENCES asop_files(id),
                metric TEXT NOT NULL, year INTEGER NOT NULL, period_kind TEXT NOT NULL,
                category_id TEXT NOT NULL, flagged INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS asop_period ON asop_observations(metric,period_kind,year);
        """)
        with connection:
            connection.execute("DELETE FROM asop_observations")
            connection.execute("DELETE FROM asop_files")
            for source in snapshot["source_files"]:
                connection.execute("INSERT INTO asop_files VALUES (?,?)", (source["id"], json.dumps(source, ensure_ascii=False)))
            for row in snapshot["observations"]:
                connection.execute("INSERT INTO asop_observations VALUES (?,?,?,?,?,?,?,?)", (
                    row["id"], row["source_file_id"], row["metric"], row["year"], row["period_kind"],
                    row["category_id"], int(bool(row["quality_flags"])), json.dumps(row, ensure_ascii=False)))
            connection.execute("INSERT INTO observation_sources VALUES (?,?) ON CONFLICT(id) DO UPDATE SET metadata_json=excluded.metadata_json", (DATASET_ID, json.dumps(metadata, ensure_ascii=False)))
    finally:
        connection.close()
    return metadata


def read_asop(database_path, metric=None, year=None, period_kind="year", source_file_id=None, include_flagged=False):
    path = Path(database_path)
    empty = {"dataset_id": DATASET_ID, "rows": [], "source_files": [], "note": "ASOP observations have not been imported"}
    if not path.exists():
        return empty
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='asop_observations'").fetchone():
            return empty
        conditions, values = ["period_kind = ?"], [period_kind]
        for column, value in (("metric", metric), ("year", year), ("source_file_id", source_file_id)):
            if value is not None:
                conditions.append(column + " = ?")
                values.append(value)
        if not include_flagged:
            conditions.append("flagged = 0")
        rows = [json.loads(row[0]) for row in connection.execute("SELECT payload_json FROM asop_observations WHERE " + " AND ".join(conditions) + " ORDER BY year,metric,category_id,source_file_id", values)]
        used = {row["source_file_id"] for row in rows}
        sources = [json.loads(row[0]) for row in connection.execute("SELECT metadata_json FROM asop_files ORDER BY id")]
        sources = [source for source in sources if source["id"] in used]
        return {"dataset_id": DATASET_ID, "rows": rows, "source_files": sources,
                "filters": {"metric": metric, "year": year, "period_kind": period_kind, "source_file_id": source_file_id, "include_flagged": include_flagged},
                "note": "Агрегаты АСОП. Повторные публикации сохранены; выберите один source_file_id перед суммированием. Год и квартал не объединяются. Это не тарифы и не подтверждённые грузовые услуги."}
    finally:
        connection.close()
