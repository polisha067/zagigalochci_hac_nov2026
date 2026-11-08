"""Normalize reference material without inventing operational links or capacities."""
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from backend.network.observations import connect


def read_port_scrape(path):
    path=Path(path)
    if path.stat().st_size > 20_000_000:
        raise ValueError('Port reference file exceeds 20 MB')
    ports=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(ports,list) or not 1 <= len(ports) <= 1000:
        raise ValueError('Expected a list of port reference records')
    ids=set()
    for port in ports:
        if not isinstance(port,dict) or not isinstance(port.get('id'),str) or not port['id'] or port['id'] in ids:
            raise ValueError('Missing or duplicate port id')
        ids.add(port['id'])
        if not isinstance(port.get('name'),str) or not port['name'].strip():
            raise ValueError('Missing port name')
        source=urlsplit(port['source_url'])
        if source.scheme not in ('https','http') or source.hostname not in ('www.rosmorport.ru','rosmorport.ru'):
            raise ValueError('Unexpected port reference source domain')
        collected=datetime.fromisoformat(port['collected_at'].replace('Z','+00:00'))
        if collected.tzinfo is None:
            raise ValueError('Collection time must include timezone')
        if port['status'] not in ('detailed','directory_only') or not isinstance(port['sections'],list) or not isinstance(port['technical_characteristics'],list):
            raise ValueError('Unexpected scrape structure')
    return ports


def coordinates(text):
    normalized=text.replace('º','°').replace('˚','°').replace('′',"'").replace('’',"'").replace('΄',"'").replace("''",'"')
    prefix=r'(\d{1,3})\s*°\s*(\d{1,2})\s*\'(?:\s*(\d{1,2}(?:[.,]\d+)?)\s*")?\s*'
    found=[]
    for suffix,maximum in ((r'с\.?\s*ш\.?',90),(r'в\.?\s*д\.?',180)):
        matches=re.findall(prefix+suffix,normalized,flags=re.I)
        if len(matches)!=1:
            return None
        degrees,minutes,seconds=matches[0]
        d,m,s=Decimal(degrees),Decimal(minutes),Decimal(seconds.replace(',','.') or '0')
        if m>=60 or s>=60 or d+m/60+s/3600>maximum:
            return None
        found.append(float(d+m/60+s/3600))
    return {'latitude':found[0],'longitude':found[1],'role':'reported_port_location_not_terminal','source_text':text}


def quantity(raw):
    """Only a single explicit numeric quantity with a recognized unit is normalized."""
    clean=raw.replace('\u00a0',' ').strip().rstrip(';').strip()
    match=re.fullmatch(r'([0-9][0-9 ]*(?:,[0-9]+)?)\s+(тыс\.\s*)?(.+?)(?:,\s*в том числе:)?',clean)
    if not match:
        return None
    number,thousands,unit=match.groups()
    units={'тонн в год':'tonnes_per_year','ДФЭ в год':'TEU_per_year','пассажиров в год':'passengers_per_year','га':'hectares','кв. км':'km2','единиц':'count','единиц.':'count','ед.':'count','п.м.':'m','м':'m','кв. м':'m2','тонн':'tonnes'}
    if unit not in units:
        return None
    value=Decimal(number.replace(' ','').replace(',','.'))*(1000 if thousands else 1)
    return {'value':format(value,'f'),'unit':units[unit],'source_unit':('тыс. ' if thousands else '')+unit,
            'capacity_role':'declared_annual_total_not_bookable' if units[unit].endswith('_per_year') else 'reference_characteristic'}


def normalize_ports(path):
    ports=read_port_scrape(path)
    result=[]
    relevant={'Местонахождение морского порта','Основные технические характеристики морского порта','Период навигации','Перечень услуг, оказываемых в морском порту','Портовые сборы','Сведения о границах морского порта','Регистрационный номер морского порта'}
    for port in ports:
        sections=[s for s in port['sections'] if s['title'] in relevant]
        locations=[s['text'] for s in sections if s['title']=='Местонахождение морского порта']
        location=locations[0] if len(locations)==1 else ''
        nav=[s['text'] for s in sections if s['title']=='Период навигации']
        metrics=[]
        for item in port['technical_characteristics']:
            metrics.append({**item,'normalized':quantity(item['value_raw'])})
        result.append({'id':port['id'],'name':port['name'],'basin':port['basin'],'branch':port['branch'],
            'source_url':port['source_url'],'collected_at_reported':port['collected_at'],'scrape_status':port['status'],
            'verification':'user_provided_scrape_structure_checked_not_refetched','coordinates':coordinates(location) if location else None,
            'navigation_text':nav[0] if len(nav)==1 else None,'technical_characteristics':metrics,'sections':sections,
            'operational_hub':False,'available_capacity':None,'cargo_admission':None,'services':[],
            'missing_for_calculation':['specific_terminal','cargo_acceptance','handling_operations_and_prices','service_connections_and_schedules','current_available_capacity','dated_operational_windows'],
            'notes':['Портовая характеристика не гарантирует параметры каждого терминала/причала.','Круглогодичная навигация не подтверждает непрерывную работу терминала.','ДФЭ, тонны, площадь склада и свободная ёмкость не взаимозаменяемы.']})
    return {'schema_version':'1.0','dataset_id':'rosmorport-user-scrape-v1','source_file_sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            'source_filename':Path(path).name,'ports':result,'freight_services_created':0,'price_data_created':0,'operational_hubs_created':0}


def import_references(path,database_path):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    identifier=data['dataset_id']
    if identifier not in ('rosmorport-user-scrape-v1','transport-strategy-2021-v1'):
        raise ValueError('Unknown context snapshot')
    rows=data.get('ports',data.get('scenario_values',[]))
    if not isinstance(rows,list) or not rows:
        raise ValueError('Empty reference snapshot')
    if identifier=='rosmorport-user-scrape-v1' and any(r.get('operational_hub') is not False or r.get('services')!=[] for r in rows):
        raise ValueError('Reference import must not create operational hubs/services')
    metadata={'id':identifier,'kind':'reference_context','rows':len(rows),'file_sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),
              'source_file_sha256':data['source_file_sha256'],'freight_services_created':0,'price_data_created':0}
    if 'ports' in data:
        metadata.update(detailed=sum(p['scrape_status']=='detailed' for p in rows),directory_only=sum(p['scrape_status']=='directory_only' for p in rows),coordinates=sum(p['coordinates'] is not None for p in rows))
    connection=connect(database_path)
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS reference_context (dataset_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)')
        with connection:
            connection.execute('INSERT INTO reference_context VALUES (?,?) ON CONFLICT(dataset_id) DO UPDATE SET payload_json=excluded.payload_json',(identifier,json.dumps(data,ensure_ascii=False)))
            connection.execute('INSERT INTO observation_sources VALUES (?,?) ON CONFLICT(id) DO UPDATE SET metadata_json=excluded.metadata_json',(identifier,json.dumps(metadata,ensure_ascii=False)))
    finally:
        connection.close()
    return metadata
