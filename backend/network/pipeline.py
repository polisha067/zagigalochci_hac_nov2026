"""Published crude-oil route components, including points without a locality binding."""
from decimal import Decimal, ROUND_HALF_UP
from datetime import timezone, timedelta
import json
from pathlib import Path
from typing import Literal, List, Optional
from backend.calculation_models import InputModel, Text, Positive
from backend.network.models import Moment

PATH=Path(__file__).resolve().parents[1]/'data_sources/pipeline/tariffs.json'

class PipelineRequest(InputModel):
    engine: Literal['pipeline'] = 'pipeline'
    route_id: Text
    net_weight_tonnes: Positive
    shipment_at: Moment


class PipelineResponse(InputModel):
    engine: Literal['pipeline']
    status: str
    total_cost_rub: None
    request: dict
    known_components: List[dict]
    missing_components: List[str]
    operational_checks_required: List[str]
    dispatch_and_terminal_operations_added: bool
    sources: List[dict]
    warnings: List[str]


def calculate_pipeline(payload):
    request=payload if isinstance(payload,PipelineRequest) else PipelineRequest.model_validate(payload)
    data=json.loads(PATH.read_text())
    route=next((r for r in data['routes'] if r['id']==request.route_id),None)
    if route is None:raise ValueError('Неизвестный route_id трубопровода')
    # Tariff snapshot calendar is evaluated in Moscow time, explicitly fixed here.
    local_day=request.shipment_at.astimezone(timezone(timedelta(hours=3))).date().isoformat()
    if not data['valid_from']<=local_day<data['valid_until_exclusive']:
        return {'engine':'pipeline','status':'outside_snapshot_validity','total_cost_rub':None,'request':request.model_dump(mode='json'),
                'known_components':[],'missing_components':['current_tariff'],'operational_checks_required':[],
                'dispatch_and_terminal_operations_added':False,'sources':data['sources'],'warnings':['Дата вне границ тарифного снимка.']}
    amount=Decimal(str(request.net_weight_tonnes))*Decimal(route['rate_rub'])
    return {'engine':'pipeline','status':'partial_tariff','total_cost_rub':None,'request':request.model_dump(mode='json'),
            'known_components':[{'route_id':route['id'],'amount_excluding_vat_rub':float(amount.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)),
                                'rate_rub':route['rate_rub'],'basis':route['basis'],'source_id':route['source_id']}],
            'missing_components':route['missing_components'],
            'operational_checks_required':['oil_quality_and_hazardous_admission','origin_and_destination_sikn','capacity_and_acceptance_schedule','losses_and_delivered_net_mass'],
            'dispatch_and_terminal_operations_added':False,'sources':data['sources'],
            'warnings':['Опубликованный компонент стоимости не подтверждает приём груза, срок и полную цену.','Маршрутный/сетевой тариф за тонну не умножается на длину.']}
