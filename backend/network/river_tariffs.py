"""Additional published forwarding packages on the Yenisei."""

from copy import deepcopy
from pathlib import Path
import json

from backend.network.provider_tariffs import observations, rate_evidence, source


RIVER_SNAPSHOT = Path(__file__).resolve().parents[1] / "data_sources/river/network.json"


def river_catalog():
    catalog = json.loads(RIVER_SNAPSHOT.read_text(encoding="utf-8"))
    collected, records, documents, _ = observations()
    templates = {item["id"]: item for item in catalog["services"]}
    hubs = list(catalog["hubs"])
    opening = records["morflot-turukhansk-dudinka-opening-2026"]
    opening_id = "morflot-yenisei-opening-2026"
    catalog["sources"].append({"id": opening_id, "name": "Росморречфлот — открытие северного участка Енисея",
        "url": documents[opening["source"]], "status": "fact", "roles": ["navigation_reference"]})
    for record_id, provider_id, name, routes, end_date, vat_rate in (
        ("krasrp-yenisei-container", "krasrp-2026", "Красноярский речной порт",
         [("down", "Дудинка (порт)")], "2026-10-01", None),
        ("enisey-nord-yenisei-container", "enisey-nord-2026", "Енисей-Норд",
         [("down", "г. Красноярск - порт Дудинка"), ("up", "порт Дудинка – порт Красноярск")],
         records["enisey-nord-yenisei-container"]["published_end_date"], 0.22),
    ):
        record = records[record_id]
        catalog["sources"].append(source(provider_id, f"{name} — контейнерный прейскурант", documents[record["source"]]))
        start = opening["opening_date"] + "T00:00:00+07:00"
        end = end_date + "T00:00:00+07:00"
        evidence = rate_evidence(provider_id, collected, start, end)
        provider_hubs = {}
        for template in hubs:
            hub = deepcopy(template)
            hub["id"] = f"{provider_id}:{template['id']}"
            hub["name"] = f"{hub['city']} — речной порт, {name}"
            for side in ("entry", "exit"):
                hub[side]["resource_id"] = f"{hub['id']}:{side}"
            catalog["hubs"].append(hub)
            provider_hubs[template["id"]] = hub["id"]
        for direction, route in routes:
            table = record["ordinary_rows"] if vat_rate is None else record["rows"]
            row = next(row for row in table if row[0] == route)
            rates = (row[6], row[8]) if vat_rate is None else tuple(row[3:5])
            for size, rate in zip((20, 40), rates):
                service = deepcopy(templates[f"river-{size}-{direction}"])
                service_id = f"river-{provider_id}:{direction}:{size}"
                service.update(
                    id=service_id, resource_id=service_id, from_hub=provider_hubs[service["from_hub"]],
                    to_hub=provider_hubs[service["to_hub"]], windows=[{"start": start, "end": end}],
                    timetable={"kind": "interval", "anchor": start, "every_hours": 96},
                    quote={"component": "transport", "basis": "per_unit", "rate_rub": rate,
                           "tax_basis": "including_vat", "tax_rate": vat_rate, "evidence": evidence},
                    additional_charges=[], storage_quotes={}, terminal_handling_included=True,
                    reference_metadata={"tariff_source_id": provider_id, "container_size_ft": size,
                        "provider_name": name,
                        "scope": "forwarder_container_port_to_port", "navigation_source_id": opening_id,
                        "schedule_status": "scenario"},
                )
                service["evidence"]["source_ids"].append(opening_id)
                catalog["services"].append(service)
    return catalog
