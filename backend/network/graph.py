import hashlib
import json
from pathlib import Path

from backend.network.models import NetworkCatalog


CATEGORIES = [
    ("road", "Автомобильный"), ("rail", "Железнодорожный"),
    ("sea", "Морской"), ("river", "Речной"), ("air", "Воздушный"),
    ("pipeline", "Трубопроводный"), ("multimodal", "Мультимодальный"),
]


class FreightNetwork:
    def __init__(self, catalog):
        self.catalog = catalog if isinstance(catalog, NetworkCatalog) else NetworkCatalog.model_validate(catalog)
        self.hubs = {hub.id: hub for hub in self.catalog.hubs}
        self.services = {service.id: service for service in self.catalog.services}
        self.outgoing = {}
        self.transfers = {}
        for service in sorted(self.catalog.services, key=lambda value: value.id):
            self.outgoing.setdefault(service.from_hub, []).append(service)
        for transfer in self.catalog.transfers:
            self.transfers.setdefault((transfer.hub_id, transfer.from_mode, transfer.to_mode), []).append(transfer)
        raw = json.dumps(self.catalog.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self.fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def transfer(self, hub_id, previous, following):
        matches = [rule for rule in self.transfers.get((hub_id, previous.mode, following.mode), [])
                   if (not rule.from_service_id or rule.from_service_id == previous.id)
                   and (not rule.to_service_id or rule.to_service_id == following.id)]
        # Специальная пара услуг имеет приоритет, но одинаковая специфичность неоднозначна.
        if not matches:
            return None
        specificity = max(bool(rule.from_service_id) + bool(rule.to_service_id) for rule in matches)
        matches = [rule for rule in matches if bool(rule.from_service_id) + bool(rule.to_service_id) == specificity]
        return matches[0] if len(matches) == 1 else None

    def paths(self, request):
        needed_hubs = {request.origin_hub_id, request.destination_hub_id, *request.via_hub_ids}
        for cargo in request.cargoes:
            needed_hubs.update((cargo.pickup_hub_id or request.origin_hub_id, cargo.delivery_hub_id or request.destination_hub_id))
        if request.network_id != self.catalog.id:
            raise ValueError("Неизвестный network_id")
        if any(hub not in self.hubs for hub in needed_hubs):
            raise ValueError("Неизвестный хаб: выберите id из /network/options")
        if any(cargo.cargo_type not in self.catalog.cargo_types for cargo in request.cargoes):
            raise ValueError("Неизвестный тип груза в каталоге сети")

        def acceptable(legs):
            nodes = [request.origin_hub_id] + [leg.to_hub for leg in legs]
            if nodes[-1] != request.destination_hub_id or not needed_hubs.issubset(nodes):
                return False
            if len(nodes) != len(set(nodes)):
                return False
            if request.required_modes and [leg.mode for leg in legs] != request.required_modes:
                return False
            if [nodes.index(hub) for hub in request.via_hub_ids] != sorted(nodes.index(hub) for hub in request.via_hub_ids):
                return False
            return all(nodes.index(cargo.pickup_hub_id or request.origin_hub_id) < nodes.index(cargo.delivery_hub_id or request.destination_hub_id) for cargo in request.cargoes)

        if request.service_ids is not None:
            if len(request.service_ids) > request.max_legs:
                raise ValueError("Число этапов превышает max_legs")
            if any(identifier not in self.services for identifier in request.service_ids):
                raise ValueError("Неизвестный service_id")
            legs = [self.services[identifier] for identifier in request.service_ids]
            if legs[0].from_hub != request.origin_hub_id or any(left.to_hub != right.from_hub for left, right in zip(legs, legs[1:])) or not acceptable(legs):
                raise ValueError("Цепочка не связана, не посещает нужные хабы в порядке погрузки/выгрузки или содержит цикл")
            return [legs], {"explicit": True, "truncated": False, "expansions": 0, "scope": "specified_itinerary"}

        paths, expansions, truncated = [], 0, False
        stack = [(request.origin_hub_id, [], {request.origin_hub_id})]
        while stack:
            if expansions >= 5000 or len(paths) >= request.max_paths:
                truncated = True
                break
            node, legs, visited = stack.pop()
            expansions += 1
            if node == request.destination_hub_id:
                if acceptable(legs):
                    paths.append(legs)
                continue
            if len(legs) >= request.max_legs:
                continue
            for service in reversed(self.outgoing.get(node, [])):
                if service.to_hub in visited:
                    continue
                if request.required_modes and (len(legs) >= len(request.required_modes) or service.mode != request.required_modes[len(legs)]):
                    continue
                stack.append((service.to_hub, legs + [service], visited | {service.to_hub}))
        return paths, {"explicit": False, "truncated": truncated, "expansions": expansions, "scope": "bounded_simple_paths; not_global_optimum"}


def load_network(path=None):
    path = path or Path(__file__).resolve().parents[1] / "examples/network.json"
    return FreightNetwork(json.loads(Path(path).read_text(encoding="utf-8")))
