import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from backend.network.engine import calculate_network
from backend.network.graph import FreightNetwork
from backend.network.models import NetworkRequest, NetworkResponse
from backend.network.resources import Ledger


class NetworkTests(unittest.TestCase):
    def setUp(self):
        folder=Path(__file__).resolve().parent/'examples'
        self.catalog=json.loads((folder/'network.json').read_text(encoding='utf-8'))
        self.request=json.loads((folder/'network-request.json').read_text(encoding='utf-8'))

    def calculate(self):
        return calculate_network(self.request,FreightNetwork(self.catalog))

    def direct(self,mode):
        self.request['required_modes']=[mode]

    def service(self,identifier):
        return next(s for s in self.catalog['services'] if s['id']==identifier)

    def hub(self,identifier):
        return next(s for s in self.catalog['hubs'] if s['id']==identifier)

    def first(self):
        return self.calculate()['candidates'][0]

    def test_three_and_four_mode_chains(self):
        result=self.calculate()
        NetworkResponse.model_validate(result)
        self.assertEqual(result['candidates'][0]['modes'],['road','sea','air'])
        self.assertEqual(result['candidates'][0]['status'],'feasible')
        self.assertEqual(result['candidates'][0]['total_cost_rub'],100071.75)
        self.request['required_modes']=['road','rail','air','sea']
        self.assertEqual(self.first()['total_cost_rub'],114226.75)

    def test_seven_categories_and_six_physical_rules(self):
        self.assertEqual(len(self.calculate()['categories']),7)
        for mode in ('road','rail','sea','river','air'):
            with self.subTest(mode=mode):
                self.direct(mode)
                self.assertEqual(self.first()['status'],'feasible')
        self.direct('pipeline')
        self.request['cargoes'][0].update(cargo_type='test_liquid',packaging='bulk_liquid')
        result=self.first()
        self.assertEqual(result['status'],'feasible')
        self.assertAlmostEqual(result['max_delivery_hours'],12.7)
        transport=next(step for step in result['steps'] if step['kind']=='transport')
        self.assertEqual(transport['flow_hours'],2)

    def test_pipeline_rejects_solid_and_packaging_mismatch(self):
        self.direct('pipeline')
        self.assertEqual(self.first()['status'],'infeasible')
        self.request['cargoes'][0]['cargo_type']='test_liquid'
        self.assertEqual(self.first()['status'],'infeasible')

    def test_missing_transfer_is_not_inferred_from_same_hub(self):
        self.catalog['transfers']=[]
        result=self.first()
        self.assertEqual(result['status'],'insufficient_data')
        self.assertEqual(result['issues'][0]['code'],'missing_transfer')
        self.assertIsNone(result['total_cost_rub'])

    def test_same_city_does_not_connect_different_hubs(self):
        for hub in self.catalog['hubs']:hub['city']='Один город'
        self.catalog['services']=[s for s in self.catalog['services'] if s['id']!='sea-to-air']
        result=self.calculate()
        self.assertEqual(result['candidates'],[])
        self.assertIsNone(result['recommendation'])
        self.assertIn('загруженная сеть',result['warnings'][0])

    def test_explicit_itinerary_checks_continuity(self):
        self.request['service_ids']=['road-to-sea','sea-to-air','air-to-destination']
        self.assertEqual(self.first()['status'],'feasible')
        self.request['service_ids']=['road-to-sea','air-to-destination']
        with self.assertRaises(ValueError):self.calculate()

    def test_multimodal_is_not_a_physical_service_mode(self):
        self.catalog['services'][0]['mode']='multimodal'
        with self.assertRaises(ValidationError):FreightNetwork(self.catalog)

    def test_verified_only_does_not_promote_synthetic_data(self):
        self.request['data_policy']='verified_only'
        self.assertEqual(self.first()['status'],'insufficient_data')
        self.assertIsNone(self.calculate()['recommendation'])

    def test_geometry_source_cannot_become_tariff(self):
        self.direct('road')
        self.service('direct-road')['quote']['evidence']['status']='fact'
        self.catalog['sources'][0]['roles']=['physical_topology']
        result=self.first()
        self.assertEqual(result['issues'][0]['code'],'source_scope')
        self.assertIsNone(result['total_cost_rub'])

    def test_air_volumetric_weight_is_from_tariff(self):
        self.direct('air')
        self.request['cargoes'][0]['weight_tonnes']=1
        result=self.first()
        cost=next(item['amount_rub'] for item in result['cost_items'] if item['kind']=='transport')
        self.assertEqual(cost,60000) # max(1000 кг,20 м3*200)*15
        del self.service('direct-air')['quote']['volumetric_kg_per_m3']
        self.assertEqual(self.first()['status'],'insufficient_data')

    def test_air_missing_volume_and_oversized_piece(self):
        self.direct('air')
        self.request['capacity_basis']='mass_only'
        del self.request['cargoes'][0]['volume_m3']
        self.assertEqual(self.first()['status'],'insufficient_data')
        self.request['cargoes'][0]['volume_m3']=20
        self.request['cargoes'][0]['max_piece_dimensions_m']=[2,2,2]
        self.assertEqual(self.first()['status'],'infeasible')

    def test_road_gross_mass_accounts_for_tare(self):
        self.direct('road')
        self.request['cargoes'][0].update(weight_tonnes=20)
        self.service('direct-road').update(gross_limit_tonnes=22,tare_tonnes=5)
        result=self.first()
        self.assertEqual(next(s for s in result['steps'] if s['kind']=='transport')['units'],2)

    def test_rail_gauge_and_water_constraints(self):
        self.direct('rail')
        self.hub('destination')['rail_gauges_mm']=[1435]
        self.assertEqual(self.first()['issues'][0]['code'],'gauge_mismatch')
        self.direct('sea')
        self.service('direct-sea')['vessel_draft_m']=5
        self.assertEqual(self.first()['issues'][0]['code'],'draft_exceeded')
        self.service('direct-sea')['vessel_draft_m']=2
        self.service('direct-sea').update(ice_class_required=True,ice_class_confirmed=None)
        self.assertEqual(self.first()['status'],'insufficient_data')
        self.service('direct-sea')['ice_class_confirmed']=False
        self.assertEqual(self.first()['issues'][0]['code'],'ice_class')

    def test_water_bridge_clearance(self):
        self.direct('river')
        self.service('direct-river').update(max_air_draft_m=5,vessel_air_draft_m=6)
        self.assertEqual(self.first()['issues'][0]['code'],'bridge_clearance')

    def test_navigation_window_not_yearly_port_capacity(self):
        self.direct('river')
        self.service('direct-river')['windows']=[{'start':'2027-05-01T00:00:00Z','end':'2027-10-01T00:00:00Z'}]
        self.assertEqual(self.first()['status'],'infeasible')

    def test_cutoff_misses_current_flight(self):
        self.direct('air')
        self.request['schedule']['ready_at']='2026-11-09T10:00:00+03:00'
        result=self.first()
        departure=next(s['departure_at'] for s in result['steps'] if s['kind']=='transport')
        self.assertEqual(datetime.fromisoformat(departure).astimezone(timezone.utc),datetime.fromisoformat('2026-11-09T21:00:00+00:00'))

    def test_closed_destination_delays_departure_not_aircraft_wait(self):
        self.direct('road')
        self.hub('destination')['windows'][0]['start']='2026-11-11T00:00:00Z'
        step=next(s for s in self.first()['steps'] if s['kind']=='transport')
        self.assertGreaterEqual(datetime.fromisoformat(step['arrival_at']),datetime.fromisoformat('2026-11-11T00:00:00+00:00'))

    def test_shared_flight_payload_is_shared_between_shipments(self):
        self.direct('air')
        self.request['schedule']={'mode':'explicit','ready_at':['2026-11-09T08:00:00+03:00','2026-11-09T09:00:00+03:00']}
        self.request['cargoes'][0]['weight_tonnes']=6
        self.service('direct-air')['available_units']=1
        result=self.first()
        departures=[s['departure_at'] for s in result['steps'] if s['kind']=='transport']
        self.assertEqual(result['status'],'feasible')
        self.assertNotEqual(departures[0],departures[1])

    def test_fleet_is_not_reused_before_turnaround(self):
        self.direct('road')
        self.request['schedule']={'mode':'explicit','ready_at':['2026-11-09T08:00:00+03:00','2026-11-09T09:00:00+03:00']}
        self.service('direct-road')['available_units']=1
        steps=[s for s in self.first()['steps'] if s['kind']=='transport']
        self.assertGreaterEqual((datetime.fromisoformat(steps[1]['departure_at'])-datetime.fromisoformat(steps[0]['departure_at'])).total_seconds()/3600,48)

    def test_hub_storage_capacity_and_temperature(self):
        self.hub('sea_hub')['storage_tonnes']=5
        result=self.first()
        self.assertEqual(result['status'],'infeasible')
        self.assertIsNone(result['total_cost_rub'])
        self.direct('road')
        self.request['cargoes'][0]['temperature_c']=[2,8]
        self.assertEqual(self.first()['status'],'insufficient_data')
        self.hub('origin')['temperature_c']=[0,10]
        self.assertEqual(self.first()['status'],'infeasible')

    def test_shelf_life_is_checked_from_original_ready(self):
        self.direct('road')
        self.request['cargoes'][0]['shelf_life_hours']=24
        self.assertEqual(self.first()['status'],'infeasible')

    def test_consolidation_preserves_tail_and_original_volume(self):
        self.direct('rail')
        self.request['schedule']={'mode':'explicit','ready_at':['2026-11-09T08:00:00+03:00','2026-11-10T08:00:00+03:00','2026-11-11T08:00:00+03:00']}
        self.request['group_sizes']=[2,3]
        self.service('direct-rail')['min_batch_tonnes']=20
        result=self.calculate()
        bad,good=result['candidates']
        self.assertFalse(bad['complete'])
        self.assertIsNone(bad['total_cost_rub'])
        self.assertTrue(good['complete'])
        self.assertEqual(sum(d['weight_tonnes'] for d in good['deliveries']),30)
        self.assertEqual(len(good['deliveries']),3)

    def test_group_larger_than_plan_is_not_silently_dropped(self):
        self.request['group_sizes']=[2]
        self.assertEqual(self.first()['status'],'feasible')

    def test_intermediate_pickup_and_delivery_conserve_cargo(self):
        self.request['required_modes']=['road','rail','air','sea']
        self.request['cargoes'].append(dict(self.request['cargoes'][0],id='sugar',cargo_type='sugar',pickup_hub_id='rail_hub',delivery_hub_id='sea_hub',ready_offset_hours=24))
        result=self.first()
        self.assertEqual(result['status'],'feasible')
        self.assertEqual([s['weight_tonnes'] for s in result['steps'] if s['kind']=='transport'],[10,20,20,10])
        self.assertEqual(sum(d['weight_tonnes'] for d in result['deliveries']),20)

    def test_incompatible_cargo_not_assigned_to_shared_vehicle(self):
        self.direct('road')
        self.request['cargoes'].append(dict(self.request['cargoes'][0],id='sugar',cargo_type='sugar'))
        self.catalog['compatibility']['flour|sugar']=False
        self.assertEqual(self.first()['status'],'infeasible')

    def test_missing_rate_and_bundled_price_not_zero(self):
        self.direct('road')
        self.service('direct-road')['quote']['rate_rub']=None
        result=self.first()
        self.assertEqual(result['status'],'insufficient_data')
        self.assertIsNone(result['total_cost_rub'])
        self.service('direct-road')['quote'].update(rate_rub=1,component='all_in')
        self.assertEqual(self.first()['issues'][0]['code'],'bundled_quote')

    def test_tax_basis_conversion_requires_explicit_rate(self):
        self.direct('road')
        self.service('direct-road')['quote'].update(tax_basis='excluding_vat',tax_rate=0.1)
        cost=next(c['amount_rub'] for c in self.first()['cost_items'] if c['kind']=='transport')
        self.assertEqual(cost,33000)
        self.service('direct-road')['quote']['tax_rate']=None
        self.assertEqual(self.first()['status'],'insufficient_data')

    def test_resource_schema_conflicts_and_unknown_request_fields(self):
        self.catalog['hubs'][0]['exit']['lanes']=3
        with self.assertRaises(ValidationError):FreightNetwork(self.catalog)
        with self.assertRaises(ValidationError):NetworkRequest.model_validate(dict(self.request,tariff=1))

    def test_money_reconciles_and_requests_do_not_book_real_capacity(self):
        before=copy.deepcopy((self.catalog,self.request))
        first=self.calculate()
        row=first['candidates'][0]
        self.assertEqual(sum((Decimal(str(c['amount_rub'])) for c in row['cost_items']),Decimal(0)),Decimal(str(row['total_cost_rub'])))
        self.assertEqual(first,self.calculate())
        self.assertEqual((self.catalog,self.request),before)

    def test_search_truncation_and_technical_limits(self):
        self.request.pop('required_modes')
        self.request['max_paths']=1
        self.assertTrue(self.calculate()['search']['truncated'])
        self.request['schedule']={'mode':'regular','start_at':'2026-11-09T00:00:00Z','end_at':'2026-11-10T00:00:00Z','frequency':{'count':65,'unit':'day'}}
        with self.assertRaisesRegex(ValueError,'64'):self.calculate()

    def test_ledger_half_open_intervals(self):
        ledger=Ledger()
        t=datetime(2026,11,9,tzinfo=timezone.utc)
        ledger.reserve('dock',t,t+timedelta(hours=1),1,1)
        self.assertFalse(ledger.fits('dock',t,t+timedelta(minutes=1),1,1))
        self.assertTrue(ledger.fits('dock',t+timedelta(hours=1),t+timedelta(hours=2),1,1))

    def test_shared_capacity_respects_gross_minus_tare(self):
        self.direct('air')
        self.request['cargoes'][0]['weight_tonnes']=6
        self.request['schedule']={'mode':'explicit','ready_at':['2026-11-09T08:00:00+03:00','2026-11-09T09:00:00+03:00']}
        self.service('direct-air').update(available_units=1,unit_tonnes=20,gross_limit_tonnes=12,tare_tonnes=2)
        row=self.first()
        self.assertEqual(row['status'],'feasible')
        steps=[s for s in row['steps'] if s['kind']=='transport']
        self.assertNotEqual(steps[0]['departure_at'],steps[1]['departure_at'])

    def test_user_temperature_cannot_relax_cargo_requirement(self):
        self.direct('road')
        self.catalog['cargo_types']['flour']['required_temperature_c']=[2,8]
        self.request['cargoes'][0]['temperature_c']=[-20,30]
        self.hub('origin')['temperature_c']=[0,10]
        self.assertEqual(self.first()['issues'][0]['code'],'temperature_mismatch')
        self.request['cargoes'][0]['temperature_c']=[20,30]
        self.assertEqual(self.first()['issues'][0]['code'],'conflicting_temperature')

    def test_storage_quote_must_cover_whole_custody_period(self):
        self.direct('road')
        self.hub('origin')['storage_quote']['evidence']['valid_until']='2026-11-09T05:10:00Z'
        self.assertEqual(self.first()['status'],'insufficient_data')

    def test_no_on_demand_shared_capacity_without_real_departures(self):
        self.service('direct-air')['timetable']={'kind':'on_demand'}
        with self.assertRaises(ValidationError):FreightNetwork(self.catalog)

    def test_overlapping_storage_checks_cargo_compatibility(self):
        from backend.network.engine import Item, Planner
        catalog=FreightNetwork(self.catalog)
        request=NetworkRequest.model_validate(self.request)
        planner=Planner(catalog,request)
        ready=datetime(2026,11,9,5,tzinfo=timezone.utc)
        flour=Item('flour',request.cargoes[0],ready,0)
        sugar_spec=request.cargoes[0].model_copy(update={'id':'sugar','cargo_type':'sugar'})
        sugar=Item('sugar',sugar_spec,ready,1)
        catalog.catalog.compatibility['flour|sugar']=False
        planner.storage(catalog.hubs['origin'],[flour],{'flour':ready},ready+timedelta(hours=2),0)
        from backend.network.resources import PlanningProblem
        with self.assertRaises(PlanningProblem):
            planner.storage(catalog.hubs['origin'],[sugar],{'sugar':ready+timedelta(hours=1)},ready+timedelta(hours=3),1)


if __name__=='__main__':unittest.main()
