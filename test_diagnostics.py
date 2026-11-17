"""Rule semantics on artificial fixtures plus numerical/API persistence checks."""
import copy,csv,hashlib,io,json,os,tempfile,time,zipfile
from pathlib import Path
TMP=tempfile.TemporaryDirectory();os.environ['WG_RUNTIME_DIR']=TMP.name
from experiment_diagnostics import analyse,rule_protocol,FIELDS,RULES
from live_simulation import DEFAULT

def fixture():
 n=8;spec={'steps':n,'dt_s':.1};series={}
 for group in ('without_battery','with_battery'):
  series[group]=[{**{k:0. for k in FIELDS},'seq':i,'time_s':i*.1,'dt_s':.1 if i else 0.,'soc':.5,'load_kw':10.} for i in range(n+1)]
  series[group][0]['unserved_kw']=999 # dt=0 must never enter events.
 inputs=[{'step':i,'start_s':(i-1)*.1,'end_s':i*.1,**{k:v for k,v in DEFAULT.items() if k!='battery_enabled'},'load_kw':10.} for i in range(1,n+1)]
 return spec,series,inputs

def test_rules():
 spec,s,inputs=fixture();r=s['with_battery'];r[2]['unserved_kw']=3;r[3]['unserved_kw']=3;r[5]['unserved_kw']=6;r[6]['unserved_kw']=1e-7
 for i in (1,2,4):r[i]['charge_kw']=20
 for i in (5,6):r[i]['discharge_kw']=20
 for i in (6,7):r[i]['soc']=.1
 r[8]['soc']=.9;r[8]['curtail_kw']=2
 report=analyse('fixture',spec,s,inputs,{},'test-only')
 events=lambda key:[e for e in report['events'] if e['group']=='with_battery' and e['rule_id']==key]
 deficit=events('unserved');assert [(e['start_seq'],e['end_seq']) for e in deficit]==[(2,3),(5,5)]
 assert deficit[0]['peak_seq']==2 and deficit[0]['start_s']==.1 and deficit[0]['end_s']==.3 and deficit[0]['duration_s']==.2
 assert abs(deficit[0]['energy_kwh']-6*.1/3600)<1e-15
 assert [(e['start_seq'],e['end_seq']) for e in events('charge_limit')]==[(1,2),(4,4)]
 assert events('soc_lower')[0]['peak_seq']==6 and events('soc_lower')[0]['peak_value']==10 and events('soc_lower')[0]['energy_kwh'] is None
 assert events('soc_upper')[0]['peak_value']==90
 assert not [e for e in report['events'] if e['group']=='without_battery']
 assert analyse('fixture',spec,s,inputs,{},'test-only')==report
 empty=fixture();assert not analyse('empty',*empty,{},'test-only')['events']
 for mutate in ('nan','clock','input'):
  a,b,c=fixture()
  if mutate=='nan':b['with_battery'][2]['unserved_kw']=float('nan')
  elif mutate=='clock':b['without_battery'][2]['time_s']+=.01
  else:c[1]['load_kw']=20
  try:analyse('bad',a,b,c,{},'test-only');raise AssertionError('bad input accepted')
  except ValueError:pass
 print('PASS: fixture rules, exact thresholds, contiguous/single-step spans, no gap bridging, tied peaks, zero-dt exclusion, SOC units, repeatability and invalid inputs')

def wait(c,id):
 end=time.monotonic()+45
 while time.monotonic()<end:
  p=c.get('/api/bench/jobs/'+id).json()
  if p['meta']['status'] not in ('queued','running','cancelling'):return p
  time.sleep(.01)
 raise AssertionError('worker timeout')
def test_api():
 from fastapi.testclient import TestClient
 import server
 from bench_experiments import BenchManager
 with TestClient(server.app) as c:
  initial=c.get('/api/live/state').json();repeat=[]
  for scenario in ('load_step','export_cap','cloud_pass','load_step'):
   job=c.post('/api/bench/jobs',json={'scenario':scenario,'duration_s':30}).json();id=job['id']
   assert c.get('/api/bench/jobs/'+id+'/diagnostics').status_code==409
   assert c.get('/api/bench/jobs/'+id+'/diagnostics/export').status_code==409
   detail=wait(c,id);assert detail['meta']['status']=='completed',detail
   response=c.get('/api/bench/jobs/'+id+'/diagnostics');assert response.status_code==200,response.text
   report=response.json();assert report['id']==id and report['numeric_sha256']==detail['result']['numeric_sha256']
   assert report['protocol']==detail['protocol']['diagnostics']
   for group in ('with_battery','without_battery'):
    rows=detail['series'][group]
    for rule in RULES:
     events=[e for e in report['events'] if e['group']==group and e['rule_id']==rule['id']]
     expected=[]
     for row in rows[1:]:
      v=row[rule['metric']];t=rule['threshold'];hit=(v>t if rule['operator']=='>' else v>=t if rule['operator']=='>=' else v<=t)
      if hit and (group=='with_battery' or not rule['battery_only']):expected.append(int(row['seq']))
     actual=[i for e in events for i in range(e['start_seq'],e['end_seq']+1)];assert actual==expected
     for e in events:
      assert e['evidence']['sample']['seq']==e['peak_seq'] and e['evidence']['input']['step']==e['peak_seq']
      if e['energy_kwh'] is not None:assert abs(e['energy_kwh']-sum(rows[i][rule['metric']]*rows[i]['dt_s']/3600 for i in range(e['start_seq'],e['end_seq']+1)))<1e-12
     if rule['id'] in ('unserved','curtail'):
      key=rule['metric'][:-2]+'kwh';assert abs(sum(e['energy_kwh'] for e in events)-detail['result']['metrics'][group][key])<1e-9
   exported=c.get('/api/bench/jobs/'+id+'/diagnostics/export');assert exported.status_code==200 and exported.json()==report
   with zipfile.ZipFile(io.BytesIO(c.get('/api/bench/jobs/'+id+'/export').content)) as z:
    assert json.loads(z.read('diagnostics.json'))==report
    manifest=json.loads(z.read('manifest.json'))
    for name,h in manifest.items():assert hashlib.sha256(z.read(name)).hexdigest()==h
   # A re-opened manager reads saved evidence without running the rule algorithm again.
   import bench_experiments
   saved=bench_experiments.analyse
   try:
    bench_experiments.analyse=lambda *a,**k:(_ for _ in ()).throw(AssertionError('reanalysed'))
    assert BenchManager(server.BENCH.root).diagnostics(id)==report
   finally:bench_experiments.analyse=saved
   if scenario=='load_step':repeat.append([{k:v for k,v in e.items()} for e in report['events']])
   path=server.BENCH.root/id/'diagnostics.json';raw=path.read_bytes();path.write_bytes(raw+b'edit')
   assert c.get('/api/bench/jobs/'+id+'/diagnostics').status_code==409
   assert c.get('/api/bench/jobs/'+id+'/diagnostics/export').status_code==409
   path.write_bytes(raw)
   # Legacy record (no diagnostics artifact) is still viewable, never re-labelled silently.
   meta_path=path.parent/'meta.json';meta=json.loads(meta_path.read_text());meta['artifact_sha256'].pop('diagnostics.json');meta_path.write_text(json.dumps(meta));server.BENCH.live_meta=None
   assert c.get('/api/bench/jobs/'+id).status_code==200
   old=c.get('/api/bench/jobs/'+id+'/diagnostics');assert old.status_code==409 and '旧版实验' in old.json()['detail']
  assert repeat[0]==repeat[1]
  assert c.get('/api/live/state').json()==initial
 print('PASS: 3 real scenarios, exact sample coverage/integrals, input evidence, deterministic repeat, source hashes, export identity, reopen without reanalysis, corruption rejection, legacy and live isolation')
if __name__=='__main__':test_rules();test_api()
