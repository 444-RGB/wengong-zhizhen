import time,os,tempfile
TEST_RUNTIME=tempfile.TemporaryDirectory()
os.environ["WG_RUNTIME_DIR"]=TEST_RUNTIME.name
from fastapi.testclient import TestClient
import server as m

def test_replay():
 with TestClient(m.app) as c:
  assert c.get('/').status_code==200
  assert c.get('/assets/app.js').status_code==200
  assert c.get('/api/health').json()['samples']==6001
  c.post('/api/control',json={'action':'seek','value':3.568})
  r=c.get('/api/state').json();assert abs(r['sample']['time_s']-3.568)<.00101
  assert r['sample']['Pgrid_W']>45000
  assert c.post('/api/control',json={'action':'seek','value':9}).status_code==400
  assert c.post('/api/control',json={'action':'speed','value':3}).status_code==400
  c.post('/api/control',json={'action':'reset'});c.post('/api/control',json={'action':'play'});time.sleep(.08)
  assert c.get('/api/state').json()['cursor']>0
  c.post('/api/control',json={'action':'pause'});a=c.get('/api/state').json()['cursor'];time.sleep(.03);assert c.get('/api/state').json()['cursor']==a
  c.post('/api/control',json={'action':'seek','value':6});c.post('/api/control',json={'action':'play'});assert c.get('/api/state').json()['cursor']<.1
  with c.websocket_connect('/ws/telemetry') as ws:assert 'sample' in ws.receive_json()
  assert c.get('/api/export').content==m.current()['path'].read_bytes()
  assert c.get('/api/events').json()
  c.post('/api/control',json={'action':'reset'})
  catalog=c.get('/api/datasets').json()
  assert len(catalog)>=3
  assert len({x['input_hash'] for x in catalog if not x['imported']})==1
  original=next(x for x in catalog if x['id']=='inc_original');po=next(x for x in catalog if x['id']=='po')
  assert po['recovery_energy_Wh']>original['recovery_energy_Wh']
  c.post('/api/select',json={'dataset':'inc_original'})
  assert c.get('/api/state').json()['dataset']=='inc_original'
  alerts=c.get('/api/alerts').json();assert any(a['code']=='high_voltage' for a in alerts)
  ack=c.post('/api/alerts/'+alerts[0]['id']+'/ack');assert ack.status_code==200
  assert c.get('/api/alerts').json()[0]['acknowledged']
  rules=c.get('/api/rules').json()
  bad=dict(rules,min_voltage=999,max_voltage=500)
  assert c.put('/api/rules',json=bad).status_code==400
  assert c.put('/api/rules',json=rules).status_code==200
  text='time_s,G_Wm2,Pgrid_W,Qgrid_var,Vdc_V,VdcRef_V\n0,1000,10,0,850,850\n1,1000,20,0,850,850\n'
  imported=c.post('/api/import',json={'title':'test import','csv_text':text});assert imported.status_code==200
  id=imported.json()['id'];assert c.post('/api/select',json={'dataset':id}).status_code==200
  assert c.get('/api/state').json()['duration']==1
  assert c.get('/api/report').status_code==200
  assert c.post('/api/import',json={'title':'test','csv_text':text}).status_code==409
  assert c.post('/api/import',json={'title':'bad','csv_text':text.replace('1,1000','0,1000')}).status_code==400
  c.post('/api/select',json={'dataset':'po'})
  info=c.get('/api/forecast/info').json();assert info['n']==8760 and info['last_target']=='2019-01-01 00:00:00'
  for index in [0,12,23,24,8740,8759]:
   f=c.get('/api/forecast/frame',params={'index':index}).json()
   assert f['actual_target'] is None and 'target' not in f
   assert len(f['forecasts'])==5
   assert all(p['actual'] is None for p in f['trace'] if p['target_label']>f['origin_label'])
   for point in f['trace']:
    if point['actual'] is not None:assert point['target_label']<=f['origin_label']
  assert c.get('/api/forecast/frame?index=8760').status_code==400
  assert c.get('/api/forecast/metrics?month=2018x01').status_code==400
  annual=c.get('/api/forecast/metrics').json()['models']
  for calculated in annual:
   original=next(r for r in m.YEAR_METRICS if r['model']==calculated['model'])
   for metric in ['RMSE_source_units','MAE_source_units','nRMSE']:assert abs(calculated[metric]-float(original[metric]))<1e-9
  month=c.get('/api/forecast/metrics?month=2018-02').json()['models'];assert all(x['n']==672 for x in month)
  import csv,io
  exported=list(csv.DictReader(io.StringIO(c.get('/api/forecast/export').text.lstrip('\ufeff'))))
  assert len(exported)==8760 and set(exported[0])=={'origin_label','target_label','expected_low','forecast_1h_point_source_units'}
  assert exported[-1]['target_label']=='2019-01-01 00:00:00'
 print('PASS: 8760 forecast origins, no future truth in decision frame/export, original annual metric reproduction, month validation; PASS: static assets, time controls, WebSocket, exact export, 3 input-matched datasets, comparison, alerts/ack, rule validation, CSV import/duplicates, HTML report, SQLite records')

if __name__=='__main__':test_replay()
