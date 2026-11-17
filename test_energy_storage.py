import os,tempfile,math,csv,io,json,copy
from pathlib import Path
TEST_RUNTIME=tempfile.TemporaryDirectory();os.environ['WG_RUNTIME_DIR']=TEST_RUNTIME.name
from fastapi.testclient import TestClient
import server as m
from storage_sandbox import run,baseline,fixture,battery_parameters,constraints

def test_energy_storage():
 with TestClient(m.app) as c:
  info=c.get('/api/energy/info').json();assert info['n_per_seed']==2927 and info['metadata']['usable_as_certified_interval_energy_kwh'] is False
  originals=list(csv.DictReader((m.ENERGY_DIR/'all_seed_metrics.csv').open(encoding='utf-8-sig')))
  for seed in [20261026,20261027,20261028]:
   f=c.get('/api/energy/frame',params={'seed':seed,'index':12}).json();assert f['actual_target'] is None
   assert all(r['actual'] is None for r in f['trace'] if r['target_label']>f['origin_label'])
   metrics=c.get('/api/energy/metrics',params={'seed':seed}).json()['models']
   for metric in metrics:
    reference=next(r for r in originals if int(r['seed'])==seed and r['model']==metric['model'])
    assert abs(metric['RMSE']-float(reference['RMSE_source_power_hour']))<1e-9
    assert abs(metric['MAE']-float(reference['MAE_source_power_hour']))<1e-9
   text=c.get('/api/energy/export',params={'seed':seed}).text;rows=list(csv.DictReader(io.StringIO(text.lstrip('\ufeff'))));assert len(rows)==2927
   assert set(rows[0])=={'origin_label','interval_end_label','forecast_integral_approx_source_power_hour','expected_low'}
  assert c.get('/api/energy/frame?seed=1').status_code==400 and c.get('/api/energy/frame?index=2927').status_code==400
  default=m.StorageInput().checked()
  first=c.post('/api/storage/solve',json=default).json();assert first['real_data_gate']=='BLOCKED_FOR_REAL_COST_EXPERIMENT'
  for name,s in first['strategies'].items():assert s['validation']['passed'] and s['validation']['terminal_soc']>=default['initial_soc']-1e-7
  assert first['strategies']['milp']['objective_currency']<=first['strategies']['reserve_rule']['objective_currency']+1e-6
  for scenario in ['cloud_dip','low_solar','standby']:
   result=run({**default,'scenario':scenario})
   for s in result['strategies'].values():assert s['validation']['passed']
  assert any(v<0 for v in run({**default,'scenario':'standby'})['fixture']['pv_kwh'])
  # Independently recompute objective and reject a corrupted flow.
  f=first['fixture'];b=first['battery'];s=first['strategies']['milp']
  actual=sum(x*p-y*q+b['throughput_cost_per_kwh']*(cc+dd) for x,y,p,q,cc,dd in zip(s['grid_import_kwh'],s['grid_export_kwh'],f['buy_price'],f['sell_price'],s['charge_kwh'],s['discharge_kwh']))
  assert abs(actual-s['objective_currency'])<1e-8
  bad=copy.deepcopy(s);bad['grid_import_kwh'][0]+=1;assert not constraints(bad,f,b)['passed']
  failed=c.post('/api/storage/solve',json={**default,'scenario':'grid_pressure','grid_import_kw':0,'capacity_kwh':1,'charge_kw':0,'discharge_kw':0}).json()
  assert failed['strategies']['milp']['status']=='infeasible_or_unsolved' and failed['fixture_cost_difference'] is None
  assert c.post('/api/storage/solve',json={**default,'initial_soc':.99}).status_code==400
  assert c.post('/api/storage/solve',json={**default,'mode':'real_data'}).status_code==422
  assert c.post('/api/storage/solve',json={**default,'future_truth':[1,2]}).status_code==422
  exported=c.post('/api/storage/export',json=default).json();assert exported['config_sha256']==first['config_sha256']
  original_status=m.solver_status;m.solver_status=lambda:{'available':False}
  try:
   assert c.post('/api/storage/solve',json=default).status_code==503
   assert c.get('/api/energy/info').status_code==200 and c.get('/api/health').status_code==200
  finally:m.solver_status=original_status
 print('PASS: V30 all-seed metric reproduction, no current future truth in frame/export, fixed primary seed; PV-BESS objective, SOC/energy/rate/exclusivity audit, corrupted-flow rejection, signed standby, infeasible plan, synthetic-only gate, missing optional dependency isolation, JSON reproduction.')

if __name__=='__main__':test_energy_storage()
