"""Rebuild the market calibration from the bundled tariff and network snapshots."""

import json
from collections import Counter

from backend.network.coverage_catalog import ROOT, settlement_catalog
from backend.network.land_market import fit_snapshot, group, observations, rail_corridors, unit_class
from backend.network.russia import RussiaNetwork


if __name__ == "__main__":
    # No local tariff refresh or network request is required for reproduction.
    network = RussiaNetwork(registry=settlement_catalog())
    snapshot = fit_snapshot(network)
    output = ROOT / "land_market_calibration.json"
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    nodes = {network.resolve(row["gar_id"]) for key in ("regional_centres", "required_population_50000")
             for row in network.registry[key]}
    corridors = {(network.resolve(left), network.resolve(right)) for left, right, _ in rail_corridors()["routes"]}
    _, tariffs, _ = network.published_tariffs
    units = Counter()
    for (left, right, mode), km in observations(network).items():
        if left not in nodes or right not in nodes or (mode == "rail" and (left, right) not in corridors):
            continue
        published = {unit_class(rate) for rate in tariffs.get((left, right), ()) if rate["mode"] == mode}
        for size in (20,) if mode == "road" else (20, 40):
            model = snapshot["models"].get(f"{mode}:{size}", {})
            band = model.get("bands", {}).get(str(group(mode, km)))
            if size not in published and band and band["km_min"] <= km <= band["km_max"]:
                units[f"{mode}:{size}"] += 1
    print(json.dumps({"settlements": len(nodes), "possible_directed_pairs": len(nodes) * (len(nodes) - 1),
                      "modeled_unit_coverage_without_truck_graph": units}, ensure_ascii=False))
