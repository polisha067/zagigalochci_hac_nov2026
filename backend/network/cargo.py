"""Free cargo names are inputs; name recognition never invents carrier admission."""
from datetime import datetime, timezone
import hashlib
import json
import re
import unicodedata

from backend.network.graph import FreightNetwork
from backend.network.models import CargoProperties, CargoType, Evidence, NetworkRequest


def normalized_name(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().replace('ё', 'е').split())


def named_temperature_cargo(name):
    return bool(name and re.search(r'заморож|охлажд|морожен|молочн|\bрыб\w*', normalized_name(name)))


def prepare_cargoes(request, network):
    """Return a request-local catalogue. Never persist user declarations globally."""
    aliases = {}
    for identifier, cargo in network.catalog.cargo_types.items():
        for alias in [cargo.name, identifier, *cargo.aliases]:
            aliases.setdefault(normalized_name(alias), set()).add(identifier)
    catalogue = network.catalog.model_copy(deep=True)
    resolved, questions, descriptions = [], [], []
    user_source = 'request-cargo-declaration'
    used_declarations = False

    def ask(item, code, fields, message):
        questions.append({'cargo_id': item.id, 'code': code, 'fields': fields, 'message': message})

    for original in request.cargoes:
        item = original.model_copy(deep=True)
        identifier = item.cargo_type
        matches = aliases.get(normalized_name(item.name), set()) if item.name else set()
        if item.name:
            if len(matches) > 1:
                ask(item, 'ambiguous_cargo_name', ['name'], 'Название неоднозначно: уточните, что именно перевозите')
                identifier = None
            elif matches:
                found = next(iter(matches))
                if identifier is not None and identifier != found:
                    ask(item, 'conflicting_cargo_identity', ['name', 'cargo_type'], 'Название и ранее выбранный тип груза противоречат друг другу')
                    identifier = None
                else:
                    identifier = found
            elif identifier is not None:
                ask(item, 'unconfirmed_cargo_identity', ['cargo_type'], 'Нельзя назначить неизвестному названию свойства другого груза; удалите cargo_type и укажите свойства')
                identifier = None
        elif identifier not in catalogue.cargo_types:
            raise ValueError('Неизвестный устаревший cargo_type: передайте название в name')
        if item.packaging is None:
            ask(item, 'missing_packaging', ['packaging'], 'В какой упаковке или форме перевозится груз?')
        if item.splittable is None:
            ask(item, 'missing_divisibility', ['splittable'], 'Можно ли разделить позицию между транспортными единицами?')
        if identifier in catalogue.cargo_types:
            known = catalogue.cargo_types[identifier]
            if item.properties is not None:
                declaration = item.properties
                if (declaration.phase != known.phase or declaration.hazardous != known.hazardous
                        or declaration.handling_profile != known.handling_profile):
                    ask(item, 'conflicting_cargo_properties', ['properties'], 'Свойства противоречат известному описанию груза; требуется уточнение')
                if declaration.temperature_control_required:
                    if declaration.required_temperature_c is None:
                        ask(item, 'missing_temperature_range', ['properties.required_temperature_c'], 'Укажите допустимый температурный диапазон')
                    else:
                        bounds = [value for value in (item.temperature_c, declaration.required_temperature_c) if value is not None]
                        item.temperature_c = (max(v[0] for v in bounds), min(v[1] for v in bounds))
                        if item.temperature_c[0] > item.temperature_c[1]:
                            ask(item, "conflicting_temperature", ["temperature_c", "properties.required_temperature_c"], "Температурные требования противоречат друг другу")
                # A declaration can narrow requirements, never erase catalogue requirements.
            item.cargo_type = identifier
            if item.name is None:
                item.name = known.name
            descriptions.append({'cargo_id':item.id, 'name':item.name, 'resolution':'recognized', 'resolved_type_id':identifier,
                                 'properties_status':known.evidence.status, 'source_ids':known.evidence.source_ids})
        elif item.name and not any(q['cargo_id']==item.id and q['code'] in ('ambiguous_cargo_name','conflicting_cargo_identity','unconfirmed_cargo_identity') for q in questions):
            if item.properties is None and (item.packaging in ('bags', 'boxes') or named_temperature_cargo(item.name)):
                item.properties = CargoProperties(phase='solid', hazardous=False, handling_profile='dry_general',
                                                  temperature_control_required=item.temperature_c is not None or named_temperature_cargo(item.name),
                                                  required_temperature_c=item.temperature_c)
            if item.properties is None:
                ask(item, 'missing_cargo_properties', ['properties.phase','properties.hazardous','properties.handling_profile','properties.temperature_control_required'],
                    'Уточните состояние груза, опасность, условия обращения и необходимость температурного режима')
            elif item.properties.temperature_control_required and item.properties.required_temperature_c is None:
                ask(item, 'missing_temperature_range', ['properties.required_temperature_c'], 'Укажите допустимый температурный диапазон')
            elif item.packaging is not None:
                properties = item.properties
                identity = json.dumps([normalized_name(item.name), properties.model_dump(mode='json'), item.packaging], ensure_ascii=False, sort_keys=True)
                identifier = 'declared-' + hashlib.sha256(identity.encode()).hexdigest()[:20]
                evidence = Evidence(source_ids=[user_source], status='assumption', observed_on=datetime.now(timezone.utc).date(),
                                    valid_from='2000-01-01T00:00:00Z', valid_until='2100-12-31T23:59:59Z')
                catalogue.cargo_types[identifier] = CargoType(name=item.name, phase=properties.phase, hazardous=properties.hazardous,
                    handling_profile=properties.handling_profile, packagings=[item.packaging],
                    required_temperature_c=properties.required_temperature_c, evidence=evidence)
                item.cargo_type = identifier
                used_declarations = True
            descriptions.append({'cargo_id':item.id,'name':item.name,'resolution':'declared' if item.cargo_type else 'needs_clarification',
                                 'resolved_type_id':item.cargo_type,'properties_status':'assumption','source_ids':[user_source] if item.cargo_type else []})
        resolved.append(item)
    if questions:
        return request, network, descriptions, questions
    if used_declarations:
        catalogue.sources.append({'id':user_source, 'name':'Свойства, указанные пользователем для этого запроса',
                                  'roles':['cargo_properties'], 'status':'assumption'})
    prepared = request.model_copy(update={'cargoes':resolved})
    return prepared, FreightNetwork(catalogue), descriptions, []
