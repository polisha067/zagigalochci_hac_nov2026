"""Reproducible river scenario: published component tariffs + explicit synthetic gaps."""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
ID='river-krasnoyarsk-dudinka-scenario-v1'
START='2026-06-01T00:00:00+07:00';END='2026-10-01T00:00:00+07:00'
SOURCE='trassa-express-river-2026';SCENARIO='river-gap-scenario-v1'


def build():
    def evidence(status='assumption',source=SCENARIO):
        return dict(source_ids=[source],status=status,observed_on='2026-11-08',
                    valid_from='2026-01-01T00:00:00+07:00',valid_until='2027-01-01T00:00:00+07:00')
    def quote(component,rate,basis='per_unit',published=False):
        return dict(component=component,basis=basis,rate_rub=rate,minimum_rub=0,fixed_rub=0,
                    tax_basis='including_vat',tax_rate=.05,
                    evidence=evidence('fact',SOURCE) if published else evidence())
    scenario=[
        {'field':'navigation','value':[START,END],'status':'synthetic','reason':'Условный сезон для проверки; фактические даты навигации не опубликованы в прайсе.'},
        {'field':'departures','value':'Каждый четверг в 10:00 Красноярск, в 14:00 Дудинка, только в условный сезон','status':'synthetic'},
        {'field':'travel_hours','value':{'downstream':144,'upstream':192},'status':'synthetic','reason':'6 суток вниз, 8 суток вверх, не расписание перевозчика.'},
        {'field':'cutoff_hours','value':24,'status':'synthetic'},
        {'field':'container_20','value':{'payload_tonnes':18,'usable_m3':28,'tare_tonnes':2.3,'gross_limit_tonnes':24},'status':'synthetic'},
        {'field':'container_40','value':{'payload_tonnes':24,'usable_m3':58,'tare_tonnes':3.8,'gross_limit_tonnes':30.48},'status':'synthetic'},
        {'field':'max_piece','value':{'tonnes':2,'dimensions_m':[2,2,2],'rotation':True},'status':'synthetic'},
        {'field':'available_containers_per_departure','value':8,'status':'synthetic','reason':'Отдельный сценарный лимит каждой размерной альтернативы, не остаток мест реального судна.'},
        {'field':'handling','value':{'fixed_hours':2,'tonnes_per_hour':10,'lanes':2},'status':'synthetic'},
        {'field':'terminal_storage','value':{'tonnes':500,'m3':1500,'max_hours':240},'status':'synthetic'},
        {'field':'water','value':{'vessel_draft_m':2.0,'max_draft_m':2.5,'ice_class_required':False},'status':'synthetic','reason':'Условный летний водный путь; не гидрологические измерения и не допуск судна.'},
        {'field':'cargo_admission','value':'Условно сухие неопасные грузы в bags/boxes, без температурного режима','status':'synthetic'},
        {'field':'terminal_identity','value':'Условная площадка в Красноярске и сценарный узел на причале совхоза Полярный в Дудинке','status':'assumption','reason':'Название причала взято из прайса; реальная доступность/технология не подтверждены.'},
        {'field':'storage_rule','value':'Каждые начатые 24 часа: ceil(max(0,hours-free_hours)/24); Красноярск free=0, Дудинка free=72','status':'assumption','reason':'Ставки за контейнеро-сутки опубликованы. Способ округления и точный отсчёт требуют уточнения; 3 суток Дудинки взяты из строки о хранении более 3 суток.'},
        {'field':'tariff_year','value':'2026-01-01 <= date < 2027-01-01','status':'assumption','reason':'Страница подписана 2026 годом, точные даты ввода/отмены не опубликованы.'},
        {'field':'price_scope','value':'Контейнер клиента, без аренды, затарки, упаковки, авто подвоза и вывоза','status':'assumption','reason':'Эти расходы не включены в сценарий; нулевой платы за них перевозчик не подтверждал.'}
    ]
    raw=ROOT/'data/river/trassa-express-2026.html'
    reviewed_sha256='0d9efc1a3bb59a3124cd148c9eed5037aeeb85e0ed05d1cf9b97c603fa1618f2'
    if raw.exists() and hashlib.sha256(raw.read_bytes()).hexdigest()!=reviewed_sha256:
        raise ValueError('Прайс изменился: перед сборкой нужно проверить ставки')
    sources=[{'id':SOURCE,'name':'Трасса-Экспресс: прайс-лист 2026','url':'https://trassa-express.com/PRAIS-LIST/','roles':['tariff'],'status':'fact','observed_on':'2026-11-08',
              'sha256':reviewed_sha256,
              'scope':'Факт публикации компонентных ставок, не подтверждение рейсов/бронирования.',
              'published_rates_including_vat':{'river_20':133000,'river_40':247000,'krasnoyarsk_loaded_handling_20':12000,'krasnoyarsk_loaded_handling_40':20000,'dudinka_loaded_handling_20':20000,'dudinka_loaded_handling_40':30000,'krasnoyarsk_storage_20_per_day':300,'krasnoyarsk_storage_40_per_day':500,'dudinka_storage_20_per_day':400,'dudinka_storage_40_per_day':600},'vat_rate':.05},
             {'id':SCENARIO,'name':'Синтетическое заполнение пробелов речной схемы, версия 1','roles':['hub','handling','cargo_properties','freight_service','tariff'],'status':'synthetic','parameters':scenario}]
    cargo={'name':'Условный сухой неопасный груз','aliases':['сухой груз'],'phase':'solid','hazardous':False,'handling_profile':'dry_general','packagings':['boxes','bags'],'evidence':evidence()}
    ids=['krasnoyarsk-river-scenario','dudinka-polyarny-scenario']
    hubs=[]
    for i,(identifier,city) in enumerate(zip(ids,['Красноярск','Дудинка'])):
        def op(side):return dict(fixed_hours=2,tonnes_per_hour=10,resource_id=identifier+'-'+side,lanes=2,quote=quote('handling',0),evidence=evidence())
        hubs.append(dict(id=identifier,name='Условная речная площадка Красноярска' if i==0 else 'Причал совхоза «Полярный»: сценарный узел',city=city,kind='river_port',available=True,modes=['river'],cargo_types=['dry_general'],cargo_profiles=['dry_general'],packagings=['boxes','bags'],hazardous_allowed=False,
                         windows=[{'start':START,'end':END}],storage_tonnes=500,storage_m3=1500,max_storage_hours=240,storage_free_hours=0 if i==0 else 72,
                         storage_quote=quote('storage',0,'per_tonne_hour'),entry=op('entry'),exit=op('exit'),evidence=evidence()))
    services=[]
    def storage_quote(rate):
        q=quote('storage',rate,'per_unit_day');q['evidence']['source_ids']=[SOURCE,SCENARIO]
        return q
    for reverse in [False,True]:
        origin,target=(ids[1],ids[0]) if reverse else (ids[0],ids[1])
        dep=datetime.fromisoformat('2026-06-04T14:00:00+07:00' if reverse else '2026-06-04T10:00:00+07:00')
        departures=[]
        while dep<datetime.fromisoformat(END):departures.append(dep.isoformat());dep+=timedelta(days=7)
        for size,payload,volume,tare,gross,rate,kras,dud in [(20,18,28,2.3,24,133000,12000,20000),(40,24,58,3.8,30.48,247000,20000,30000)]:
            # The date window and origin-side details of published fees are assumptions.
            # Keep evidence for price fact separate from synthetic operations.
            services.append(dict(id=f'river-{size}-'+('up' if reverse else 'down'),from_hub=origin,to_hub=target,mode='river',available=True,
                                 cargo_types=['dry_general'],cargo_profiles=['dry_general'],packagings=['boxes','bags'],hazardous_allowed=False,windows=[{'start':START,'end':END}],
                                 timetable={'kind':'explicit','departures':departures},cutoff_hours=24,travel_hours=192 if reverse else 144,
                                 min_batch_tonnes=0,require_volume=True,allowed_phases=['solid'],unit_tonnes=payload,unit_m3=volume,gross_limit_tonnes=gross,tare_tonnes=tare,
                                 available_units=8,booking='dedicated',capacity_scope='departure',resource_id=f'river-{size}-'+('up' if reverse else 'down'),
                                 quote=quote('transport',rate,published=True),
                                 additional_charges=[{'id':'origin_container_handling','at':'departure','quote':quote('handling',dud if reverse else kras,published=True)},
                                                     {'id':'destination_container_handling','at':'arrival','quote':quote('handling',kras if reverse else dud,published=True)}],
                                 storage_quotes={ids[0]:storage_quote(300 if size==20 else 500),ids[1]:storage_quote(400 if size==20 else 600)},
                                 max_piece_tonnes=2,max_piece_dimensions_m=[2.0,2.0,2.0],allow_rotation=True,
                                 vessel_draft_m=2.0,max_draft_m=2.5,ice_class_required=False,evidence=evidence()))
    return {'id':ID,'coverage':'Сценарий Красноярск ↔ Дудинка, условная навигация 2026. Публичные компонентные цены; расписание, приём груза и ресурсы синтетические. Не предложение перевозчика.','sources':sources,'cargo_types':{'dry_general':cargo},'compatibility':{},'hubs':hubs,'services':services,'transfers':[]}


if __name__=='__main__':
    path=Path(__file__).with_name('network.json');path.write_text(json.dumps(build(),ensure_ascii=False,indent=2)+'\n')
    print(path)
