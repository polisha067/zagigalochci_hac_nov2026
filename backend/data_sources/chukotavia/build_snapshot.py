from pathlib import Path
import json,hashlib,re
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from lxml import html
root=Path(__file__).resolve().parents[2];raw=root/'data/chukotavia/raw';out=root/'data_sources/chukotavia';out.mkdir(parents=True,exist_ok=True)

# Reviewed transcription is bound to the original PDF bytes.
REVIEWED_SHA256 = {'418-0.pdf': '06478eef075d72fc5a8f2bbfe9f5f6945b24f674159198d3e39ed08b4e45795f', '466-0.pdf': 'ddae08fdfceabe6336801a381462bea8324362058390bd0d37a700e842f90dae', '420-0.pdf': 'c39686a58d9fcb6e3c63df3c0fdab33b91903307c97c1d7e55e58208d0dff048', '487-0.pdf': '2b870786e885521bbcf4b2bf3d978045483378fccbbcc35f45e8441800985477', '472-0.pdf': '47798d4b823687480feb2323fd516561461cf146d40576ae04418379485235f6', '445-0.pdf': '6a1fb6d6e35deb910b3e24c645af3ac19c7c869be5c4dda8b557ccc3bf2199b3', '488-0.pdf': '6b2a292e63aa7f395a9cc8c309f153004bb8af964851c2d33fe61b785d09336b', '468-0.pdf': '0aaaf6a4c99f0cdb91169c76e7f259effb805bf77427df6512d3095a88d69046', '442-0.pdf': 'c7f8cb07732b5b27757a48dfcca7c859e9ef1c4142ced92cda9482ae5004df6a', '463-0.pdf': '0a131abcda9b9236a6eefc4ffaafabc059d8341c6d1b90f847485125f01e57a4', '305-0.pdf': 'd40d39eb9e71f4f7b253af5ace1bff5ea83f76476f345572103bd60a8d3882ea', '305-1.pdf': 'e9122e6e383807aab4161f41738c26688e0e14f588a18169eac87b126d00007e'}
for filename, expected_hash in REVIEWED_SHA256.items():
 if hashlib.sha256((raw/filename).read_bytes()).hexdigest()!=expected_hash:
  raise ValueError("Source changed; review transcription before rebuilding: "+filename)

# Reviewed against every page of both four-page tariff tables. Values are RUB/kg excluding VAT.
pairs='''Анадырь|Усть-Белая|658|884
Анадырь|Снежное|694|935
Анадырь|Окружной|173|197
Анадырь|Канчалан|238|297
Анадырь|Краснено|554|711
Анадырь|Эгвекинот|487|569
Анадырь|Ваеги|651|715
Анадырь|Марково|590|640
Анадырь|Мейныпильгыно|707|931
Анадырь|Хатырка|865|1152
Анадырь|Алькатваам|602|781
Анадырь|Мыс Шмидта|886|1203
Певек|Айон|375|512
Алькатваам|Окружной|622|891
Анюйск|Кепервеем|595|788
Анюйск|Островное|348|491
Беринговский|Мейныпильгыно|395|525
Беринговский|Окружной|621|842
Беринговский|Хатырка|669|908
Билибино|Мыс Шмидта|492|691
Билибино|Певек|548|764
Бургахчан|Каэттын|285|404
Бургахчан|Кепервеем|587|780
Бургахчан|Омолон|661|914
Ванкарем|Нутэпэльмен|207|290
Инчоун|Уэлен|195|266
Канчалан|Краснено|363|521
Канчалан|Окружной|456|660
Каэттын|Омолон|402|554
Кепервеем|Каэттын|766|1047
Кепервеем|Илирней|417|562
Кепервеем|Омолон|960|1315
Кепервеем|Островное|323|442
Кепервеем|Певек|823|1196
Конергино|Уэлькаль|532|743
Краснено|Окружной|548|786
Лаврентия|Инчоун|344|438
Лаврентия|Нешкан|548|742
Лаврентия|Провидения|779|1008
Лаврентия|Сиреники|745|971
Лаврентия|Уэлен|345|473
Лаврентия|Эгвекинот|1098|1436
Лаврентия|Энурмино|486|671
Лаврентия|Янракыннот|416|534
Ламутское|Чуванское|212|302
Марково|Ваеги|249|369
Марково|Ламутское|395|518
Марково|Чуванское|402|542
Мейныпильгыно|Окружной|693|995
Мейныпильгыно|Хатырка|340|486
Окружной|Снежное|696|996
Окружной|Усть-Белая|733|1050
Окружной|Хатырка|851|1216
Певек|Мыс Шмидта|783|1068
Певек|Рыткучи|425|597
Провидения|Нунлингран|416|540
Провидения|Сиреники|217|272
Провидения|Эгвекинот|999|1328
Провидения|Энмелен|536|702
Янракыннот|Провидения|488|628
Ванкарем|Эгвекинот|629|839
Эгвекинот|Конергино|282|359
Эгвекинот|Мыс Шмидта|697|951
Эгвекинот|Нешкан|886|1191
Эгвекинот|Нунлингран|851|1142
Эгвекинот|Нутэпэльмен|623|831
Эгвекинот|Уэлькаль|414|541
Эгвекинот|Энмелен|697|929
Эгвекинот|Энурмино|1207|1635
Энмелен|Нунлингран|186|261
Энурмино|Нешкан|178|242
Янракыннот|Сиреники|332|459
Усть-Белая|Снежное|135|197
Анадырь|Беринговский|448|508
Анадырь|Кепервеем|672|684
Анадырь|Лаврентия|469|606
Анадырь|Певек|697|701
Анадырь|Провидения|474|622'''
rows=[]
for k,line in enumerate(pairs.splitlines()):
 a,b,old,new=line.split('|')
 for offset,(f,t) in enumerate([(a,b),(b,a)]):
  n=k*2+offset+1
  for version,rate in [('442',old),('463',new)]:
   boundaries=[30,75,120,156] if version=='442' else [34,83,132,156]
   rows.append({'id':f'tariff-{version}-{n}','source_id':'chukotavia-'+version,'row':n,'page':next(i+1 for i,end in enumerate(boundaries) if n<=end),'origin':f,'destination':t,'rate_excluding_vat_rub_per_kg':rate,'rate_including_vat_rub_per_kg':str((Decimal(rate)*Decimal('1.22')).quantize(Decimal('.01'))),'vat_rate':'0.22','valid_from':'2026-01-01' if version=='442' else '2026-11-01','valid_until_exclusive':'2026-11-01' if version=='442' else None,'status':'fact','scope':'published_cargo_tariff_not_booking','minimum_charge_kg':None,'rounding_rule':None})
# Preserve table geometry: rowspan cells and paragraph boundaries, never flatten month/day associations.
MONTHS=['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь']
def text(c):return ' '.join(c.text_content().split())
def paras(c):return [' '.join(p.text_content().split()) for p in c.xpath('.//p') if p.text_content().strip()]
def table_grid(table):
 pending={}
 for row in table.xpath('./tr|./tbody/tr'):
  cells=row.xpath('./td|./th');grid={};col=0
  for c in cells:
   while col in pending:
    ob,left=pending[col];grid[col]=ob
    if left==1:del pending[col]
    else:pending[col]=(ob,left-1)
    col+=1
   span=int(c.get('colspan','1'));rspan=int(c.get('rowspan','1'))
   for _ in range(span):
    grid[col]=c
    if rspan>1:pending[col]=(c,rspan-1)
    col+=1
  while col in pending:
   ob,left=pending[col];grid[col]=ob
   if left==1:del pending[col]
   else:pending[col]=(ob,left-1)
   col+=1
  yield [grid.get(i) for i in range(max(grid,default=-1)+1)]
# Executed by build.py after tariff transcription and table geometry helpers.
flights=[];schedules=[];issues=[]
ALIASES={'Усть белая':'Усть-Белая','Мыс шмидта':'Мыс Шмидта','Нунлигран':'Нунлингран','Мейныпильгино':'Мейныпильгыно'}
def place(s):return ALIASES.get(' '.join(s.split()).capitalize(),' '.join(s.split()).capitalize())
def parse_clock(times):
 result=[]
 for c in re.findall(r'([А-ЯЁA-Z]{2,4})\s*(\d{2}):?\s*(\d{2})(?:\s*/\s*(\d{2}):?\s*(\d{2}))?',times):
  if result and result[-1][0]==c[0] and not result[-1][3]:
   prev=result[-1];result[-1]=(prev[0],prev[1],prev[2],c[1],c[2])
  else:result.append(c)
 return result
for sid in ['512','513','514']:
 tree=html.fromstring((raw/f'page-{sid}.html').read_text());table=tree.xpath('//table')[0]
 for ri,c in enumerate(table_grid(table)):
  if len(c)<7 or c[0] is None or not re.fullmatch(r'\d+',text(c[0])):continue
  ident=f'schedule-{sid}-{text(c[0])}-{ri}';route=text(c[2]);months=paras(c[3]);days=paras(c[4]);flags=[]
  times=next((text(v) for v in reversed(c) if re.search(r'[А-ЯЁA-Z]{2,4}\s*\d{2}:?\d{2}',text(v))),text(c[-1]))
  monthgroups=[[]]
  for p in months:
   if re.fullmatch(r'-+',p):monthgroups.append([])
   else:
    tokens=p.split()
    if all(t in MONTHS for t in tokens):monthgroups[-1].extend(tokens)
    else:flags.append('conditional_schedule_without_dates')
  monthnames=[m for g in monthgroups for m in g];timegroups=re.split(r'\s*-{3,}\s*',times)
  if len(monthgroups)!=len(timegroups):flags.append('season_time_groups_mismatch')
  if len(monthnames)!=len(days):flags.append('month_day_count_mismatch')
  dates=[];date_times={};index=0
  if not flags:
   try:
    for group,clocktext in zip(monthgroups,timegroups):
     for month in group:
      for d in re.findall(r'\d+',days[index]):
       day=date(2026,MONTHS.index(month)+1,int(d)).isoformat();dates.append(day);date_times[day]=clocktext
      index+=1
   except ValueError:flags.append('invalid_calendar_date');dates=[];date_times={}
  frequency=next((text(v) for v in c[5:] if 'по датам' in text(v)),'');count=re.findall(r'(\d+)\s*рейс',frequency);stated=int(count[-1]) if count else None
  if stated is not None and len(dates)!=stated:flags.append('published_count_mismatch')
  if len(dates)!=len(set(dates)):flags.append('duplicate_dates')
  # Protect hyphens that belong to a settlement name before splitting route separators.
  protected=re.sub(r'УСТЬ\s*[-–— ]\s*БЕЛАЯ','УСТЬ_БЕЛАЯ',route,flags=re.I)
  routeparts=[place(x.replace('_','-')) for x in re.split(r'\s*[–—-]\s*',protected) if x.strip()]
  for key in ['Усть-белая']:routeparts=[('Усть-Белая' if x==key else x) for x in routeparts]
  rec={'id':ident,'source_id':'chukotavia-'+sid,'source_row':ri+1,'flight':text(c[1]),'route_raw':route,'time_raw':times,'months_raw':months,'days_raw':days,'dates':sorted(set(dates)),'stated_flights':stated,'flags':flags,'time_basis':'local_each_settlement','timezone':'Asia/Anadyr','aircraft_family':'Mi-8' if sid=='514' else ('DHC-6' if sid=='513' else 'An-24/An-26'),'segments':[]}
  if not flags:
   for groupno,clocktext in enumerate(timegroups):
    clock=parse_clock(clocktext)
    if len(clock)!=len(routeparts):flags.append('route_clock_count_mismatch');continue
    # All forward origin/destination pairs on the same flight, including intermediate stops.
    for i in range(len(clock)-1):
     for j in range(i+1,len(clock)):
      if routeparts[i]==routeparts[j]:continue
      left,right=clock[i],clock[j];dh,dm=(left[3],left[4]) if left[3] else (left[1],left[2]);ah,am=right[1],right[2]
      depmin=int(dh)*60+int(dm);arrmin=int(ah)*60+int(am)
      if arrmin<=depmin or max(int(dh),int(ah))>23 or max(int(dm),int(am))>59:flags.append('ambiguous_clock');continue
      segment={'id':ident+f'-v{groupno}-{i}-{j}','origin':routeparts[i],'destination':routeparts[j],'origin_code':left[0],'destination_code':right[0],'departure_local':dh+':'+dm,'arrival_local':ah+':'+am,'travel_minutes':arrmin-depmin,'via':routeparts[i+1:j],'same_flight':True}
      rec['segments'].append(segment)
      for day,dayclock in date_times.items():
       if dayclock!=clocktext:continue
       flights.append(dict(segment,id=segment['id']+'-'+day,schedule_id=ident,source_id=rec['source_id'],flight=rec['flight'],date=day,departure_at=day+'T'+dh+':'+dm+':00+12:00',arrival_at=day+'T'+ah+':'+am+':00+12:00',flags=[],capacity_confirmed=False))
  # A later parsing issue invalidates every segment from that source row.
  if flags:
   for f in flights:
    if f['schedule_id']==ident:f['flags']=list(flags)
   issues.append({'schedule_id':ident,'flags':flags})
  schedules.append(rec)

# Snapshot every inspected document/page, original bytes remain in ignored raw/.
pages=json.loads((raw.parent/'pages.json').read_text());sources=[]
for item in pages:
 p=raw/('page-'+item['id']+'.html')
 scope=({'442':'cargo_tariff','463':'cargo_tariff','512':'schedule','513':'schedule','514':'schedule','51':'cargo_rules','305':'cargo_rules_with_september_amendment','425':'conditional_ferry_schedule_not_applied','511':'passenger_sales_window_not_cargo_cutoff'}).get(item['id'],'ground_handling_reference_not_automatic_cargo_surcharge')
 files=[]
 for number,f in enumerate(item['files']):
  record={k:v for k,v in f.items() if k not in ['path','error']}
  record['use']='cargo_rules' if item['id']=='305' and number<2 else ('excluded_passenger_information' if item['id']=='305' else scope)
  files.append(record)
 sources.append({'id':'chukotavia-'+item['id'],'title':item['title'],'url':item['url'],'observed_on':'2026-11-08','html_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'files':files,'scope':scope})
routekeys={(r['origin'],r['destination']) for r in rows}
matched=[f for f in flights if (f['origin'],f['destination']) in routekeys and not f['flags']]
result={'schema_version':'1.0','provider':'chukotavia','observed_on':'2026-11-08','excluded_providers':['aerogruz'],'sources':sources,'tariffs':rows,'schedules':schedules,'flights':flights,'issues':issues,'rules':{'volumetric_divisor_cm3_per_kg':'6000','volumetric_kg_per_m3':'166.6666666666666666666666667','heavy_piece_threshold_kg':'80','heavy_piece_boundary_conflict':True,'maximum_cargo_plane_piece_kg_with_mechanization':'2000','maximum_cargo_plane_piece_kg_without_mechanization':'200','cargo_booking':'standby_without_guaranteed_date','cargo_cutoff_hours':None,'free_capacity_kg':None,'cargo_acceptance':None,'charge_rounding':None,'minimum_charge_kg':None,'ground_charges_included':None,'waybill_amendment_effective':'2026-09-23','waybill_format':'electronic_with_published_exceptions','waybill_fee_inclusion':None},'summary':{'tariff_rows':len(rows),'directed_tariff_routes':len(routekeys),'schedule_rows':len(schedules),'flight_legs_by_date':len(flights),'unflagged_flight_legs_matching_tariff':len(matched),'issues':len(issues),'matched_directed_routes':len({(f['origin'],f['destination']) for f in matched}),'unmatched_directed_routes':len(routekeys-{(f['origin'],f['destination']) for f in matched})}}
(out/'dataset.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result['summary'],ensure_ascii=False))
