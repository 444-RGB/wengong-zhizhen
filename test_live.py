"""Behavioral checks for backend-owned state, persistence and conservation."""
import csv,io,math,time,tempfile
from pathlib import Path
from live_simulation import Model,Runtime,DEFAULT,module_mpp
from pv_physics import module_curve

def until(predicate,timeout=3):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  if predicate():return
  time.sleep(.02)
 raise AssertionError('Timed out waiting for actual backend steps')

def test_model():
 for g,t in [(1000,25),(200,65),(1200,-10)]:
  p,v=module_mpp(g,t);reference=module_curve(g,t,3200)[2]
  assert abs(p-reference)/reference<.001 and v>0
 assert module_mpp(0,25)==(0.,0.)
 a=Model();b=Model();initial=a.last
 for _ in range(100):
  x=a.step();y=b.step()
  assert x['pv_ac_kw']==y['pv_ac_kw'] and x['soc']==y['soc']
  assert abs(x['balance_residual_kw'])<1e-7 and abs(x['battery_residual_kwh'])<1e-9
 assert abs(a.t-10)<1e-9 and a.last['cloud_x']>initial['cloud_x']
 assert a.last['temperature_C']>initial['temperature_C']
 # Exercise exact SOC boundary in one step, with no grid supply.
 a.config={**DEFAULT,'irradiance':0,'load_kw':120,'grid_import_kw':0}
 a.energy=6.00001;a.inverter=0
 x=a.step();assert x['unserved_kw']>100 and abs(a.energy-6)<1e-9
 a.config={**DEFAULT,'irradiance':1200,'load_kw':0,'grid_export_kw':0,'cloud_depth':0}
 a.energy=53.99999;a.inverter=100
 x=a.step();assert abs(a.energy-54)<1e-9 and x['curtail_kw']>0
 assert abs(x['balance_residual_kw'])<1e-7

def test_runtime():
 with tempfile.TemporaryDirectory() as d:
  r=Runtime(d);r.start_worker()
  try:
   r.command('start');until(lambda:r.state()['seq']>=3)
   r.command('pause');s=r.state();time.sleep(.15)
   assert r.state()['seq']==s['seq'] and r.state()['soc']==s['soc']
   r.configure({**DEFAULT,'load_kw':110,'grid_import_kw':0});assert r.state()['seq']==s['seq']
   r.command('speed',5);r.command('start');until(lambda:r.state()['seq']>=s['seq']+5)
   r.command('pause');assert r.state()['unserved_kw']>0
   rows=list(csv.DictReader(io.StringIO(r.export())))
   assert len(rows)==r.state()['seq'] and int(rows[-1]['seq'])==r.state()['seq']
   old=r.path;old_id=r.state()['run_id'];r.command('reset')
   assert old.exists() and old!=r.path and r.state()['run_id']!=old_id
   assert r.state()['seq']==0 and r.state()['battery_energy_kwh']==30
   assert len(list(Path(d).glob('*.jsonl')))>=2
  finally:r.close()

def test_api():
 # test_backend sets an isolated runtime directory before importing server.
 import test_backend
 from fastapi.testclient import TestClient
 import server
 with TestClient(server.app) as c:
  c.post('/api/live/control',json={'action':'reset'})
  assert c.post('/api/live/config',json={'config':{**DEFAULT,'irradiance':-1}}).status_code==422
  assert c.post('/api/live/control',json={'action':'speed','speed':11}).status_code==422
  c.post('/api/live/control',json={'action':'start'})
  with c.websocket_connect('/ws/simulation') as ws:
   first=ws.receive_json();second=ws.receive_json()
   assert second['state']['seq']>first['state']['seq'] and second['rows']
  before=c.get('/api/live/state').json()['seq']
  until(lambda:c.get('/api/live/state').json()['seq']>before+1)
  c.post('/api/live/control',json={'action':'pause'})
  s=c.get('/api/live/state').json();assert s['error'] is None
  assert len(c.get('/api/live/history').json()['rows'])==s['seq']
  assert len(list(csv.DictReader(io.StringIO(c.get('/api/live/export').text))))==s['seq']
  clear = {**DEFAULT, 'cloud_depth': 0, 'temperature_mode': 1, 'module_temperature': 25}
  affected = {**clear, 'module_temperature': 65, 'humidity': 80, 'dust_optical_depth': .4, 'soiling_pct': 25}
  assert c.post('/api/live/config', json={'config': {**affected, 'humidity': 101}}).status_code == 422
  for settings in (clear, affected):
   response = c.post('/api/live/config', json={'config': settings})
   assert response.status_code == 200
   c.post('/api/live/control', json={'action': 'start'})
   until(lambda: c.get('/api/live/state').json()['config'] == settings)
   c.post('/api/live/control', json={'action': 'pause'})
   state = c.get('/api/live/state').json()
   assert state['temperature_C'] == settings['module_temperature']
   if settings is clear:
    available_clear = state['pv_dc_available_kw']
   else:
    expected = 900 * math.exp(-.4 * 1.4) * .75
    assert abs(state['mean_irradiance'] - expected) < 1e-9
    assert state['pv_dc_available_kw'] < available_clear

 # Repeated lifespans reopen the log safely.
 with TestClient(server.app) as c:assert c.get('/api/live/state').status_code==200

if __name__=='__main__':
 test_model();test_runtime();test_api()
 print('PASS: live MPP reference, determinism, cloud/thermal progression, AC balance, SOC boundaries, headless execution, pause/speed, input change, CSV/event persistence, reset isolation, WebSocket disconnect continuity, API validation, repeated startup.')
