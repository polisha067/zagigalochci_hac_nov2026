"""Reviewed 2026 crude-oil tariffs. Route tariffs are never multiplied by distance."""
import json
from pathlib import Path

ACT='https://www.consultant.ru/document/cons_doc_LAW_520601/2ff7a8c72de3994f30496a0ccbb1ddafdaddf518/'
OPS='https://www.consultant.ru/document/cons_doc_LAW_520601/def06aaf49031753b14c7fd3066ba01246b8883d/'
DOCS='https://ptport.ru/customers/uslugi-po-transportirovke/tipovye-formy-dogovorov/dogovornaya-rabota-2026/'

def build():
    points=[('taishet','Тайшет','Тайшет',[]),('meget','ПН «Мегет»','Мегет',[]),('ankhk','АО «АНХК»','Ангарск',['450']),
            ('yaroslavl-3','Ярославль 3','Ярославль',[]),('primorsk','НБ «Приморск»','Приморск',[]),
            ('klin','Клин','Клин',[]),('ust-luga','НБ «Усть-Луга»','Усть-Луга',[]),
            ('makhachkala','Махачкала','Махачкала',[]),('sheskharis','ПК «Шесхарис»','Новороссийск',[]),
            ('zapolyarye','ГНПС «Заполярье»',None,[]),('purpe','Пурпе',None,[]),
            ('kuyumba','Куюмба','Куюмба',[]),('kozmino','НБ «Козьмино»','Находка',[])]
    routes=[('taishet','meget','507.28','route'),('taishet','ankhk','507.28','route'),
            ('yaroslavl-3','primorsk','1024.22','route'),('klin','ust-luga','1188.69','route'),
            ('makhachkala','sheskharis','562.16','route'),('zapolyarye','purpe','632.00','network'),
            ('kuyumba','taishet','1004.71','network')]
    for origin in ('purpe',):
        for destination in ('primorsk','ust-luga'):routes.append((origin,destination,'2942.40','network'))
    operations=[('meget','loading','13457.22'),('ankhk','delivery','2646.52'),('primorsk','transshipment','12622.03'),
                ('ust-luga','transshipment','19654.91'),('kozmino','transshipment','25166.68'),
                ('kozmino','rail_unloading','38675.21'),('sheskharis','transshipment_sheskharis','5592.56'),
                ('sheskharis','transshipment_grushovaya','4294.98'),('sheskharis','loading_grushovaya','24887.82')]
    return {'id':'transneft-crude-2026-v1','reviewed_on':'2026-11-08','valid_from':'2026-01-01','valid_until_exclusive':'2027-01-01',
            'validity_note':'Ставки приказа 857/25 в редакции 27.02.2026; конец 2026 — граница снимка, не утверждённая дата отмены.',
            'sources':[{'id':'pipeline-fas-857-25','url':ACT,'roles':['tariff']},{'id':'pipeline-fas-operations','url':OPS,'roles':['tariff']},
                       {'id':'pipeline-contract-2026','url':DOCS,'roles':['reference_points']},
                       {'id':'pipeline-physical-lengths','url':'https://sst.ru/upload/presentations/reference-list.pdf','roles':['physical_distance_reference']}],
            'points':[{'id':a,'name':b,'settlement_name':c,'sikn':d,'mapping_status':'unresolved' if c is None else 'locality_reference_not_exact_terminal_location'} for a,b,c,d in points],
            'routes':[{'id':'pipeline-'+a+'-'+b,'origin':a,'destination':b,'rate_rub':r,'basis':'per_tonne','tariff_type':t,
                       'tax_basis':'excluding_vat','source_id':'pipeline-fas-857-25','cost_scope':'published_route_component',
                       'missing_components':['composition_of_route_tariff','applicable_terminal_operations','vat_for_specific_operation'],
                       'reverse_confirmed':False} for a,b,r,t in routes],
            'operations':[{'point_id':a,'operation':b,'rate_rub':c,'basis':'per_100_tonnes','tax_basis':'excluding_vat','source_id':'pipeline-fas-operations',
                           'automatic_addition':False} for a,b,c in operations],
            'pumping_by_operator':[{'operator':name,'rate_rub':rate,'basis':'per_100_tonne_km','tax_basis':'excluding_vat','source_url':'https://www.consultant.ru/document/cons_doc_LAW_520601/992ed8600433c6edb1ebfac4570b10f232cb6aa1/'} for name,rate in [('Транснефть-Сибирь','16.0070'),('Транснефть-Западная Сибирь','19.9894'),('Транснефть-Восток','28.7696'),('Транснефть-Дальний Восток','28.7696'),('Транснефть-Урал','25.0049'),('Транснефть-Прикамье','27.7879'),('Транснефть-Верхняя Волга','33.1572'),('Транснефть-Приволга','26.0948'),('Транснефть-Дружба','30.2367'),('Черномортранснефть','41.6330'),('Транснефть-Север','51.8228'),('Транснефть-Балтика','30.2821')]],
            'dispatch':{'rate_rub':'50.7193','basis':'per_100_tonne_km','tax_basis':'excluding_vat','source_id':'pipeline-fas-857-25','automatic_addition':False},
            'physical_sections':[{'origin':'zapolyarye','destination':'purpe','distance_km':488,'tariff_distance_confirmed':False},
                                 {'origin':'taishet','destination_name':'Сковородино','distance_km':2694,'tariff_distance_confirmed':False},
                                 {'origin_name':'Сковородино','destination':'kozmino','distance_km':2046,'tariff_distance_confirmed':False}],
            'rules':['Только товарная нефть; нефтепродукты и газ требуют других каталогов.',
                     'Масса для оплаты — нетто в начале маршрута; потери не заданы фактическим нормативом.',
                     'Нет автоматического добавления диспетчеризации/операций к сетевому или маршрутному тарифу.',
                     'Скорость потока, сроки, свободные мощности, допуски продукта и включённые услуги не подтверждены.',
                     'Сетевой тариф ВСТО 3896.51 ₽/т применяется при условиях пп.1.11–1.12, а не к любой паре точек ВСТО; автоматически не создаётся.']}

if __name__=='__main__':
    target=Path(__file__).with_name('tariffs.json');target.write_text(json.dumps(build(),ensure_ascii=False,indent=2)+'\n');print(target)
