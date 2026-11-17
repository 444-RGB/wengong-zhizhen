"""Paired experiment numerical, isolation, persistence and API regression tests."""
import csv,hashlib,io,json,os,tempfile,time,zipfile
from pathlib import Path
TMP=tempfile.TemporaryDirectory();os.environ['WG_RUNTIME_DIR']=TMP.name
from fastapi.testclient import TestClient
import server
from bench_experiments import BenchManager,Busy,protocol,metrics

def wait(manager,id):
 until=time.monotonic()+45
 while time.monotonic()<until:
  p=manager.detail(id)
  if p['meta']['status'] not in ('queued','running','cancelling'):return p
  time.sleep(.01)
 raise AssertionError('worker timeout')
def run():
 manager=server.BENCH
 with TestClient(server.app) as c:
  initial=c.get('/api/live/state').json()
  assert c.get('/experiments').status_code==200
  assert c.post('/api/bench/jobs',json={'scenario':'bad','duration_s':30}).status_code==422
  assert c.post('/api/bench/jobs',json={'scenario':'cloud_pass','duration_s':31}).status_code==422
  results=[]
  for scenario in ('cloud_pass','load_step','export_cap','cloud_pass'):
   r=c.post('/api/bench/jobs',json={'scenario':scenario,'duration_s':30});assert r.status_code==200,r.text
   id=r.json()['id']
   assert c.post('/api/bench/jobs',json={'scenario':scenario,'duration_s':30}).status_code==409
   assert c.get('/api/bench/jobs/'+id+'/export').status_code==409
   p=wait(manager,id);assert p['meta']['status']=='completed',p
   assert p['result']['validation']['passed']
   assert len(p['series']['with_battery'])==301
   assert p['series']['with_battery'][-1]['time_s']>29.999
   z=c.get('/api/bench/jobs/'+id+'/export');assert z.status_code==200
   with zipfile.ZipFile(io.BytesIO(z.content)) as archive:
    assert archive.testzip() is None
    manifest=json.loads(archive.read('manifest.json'))
    for name,h in manifest.items():assert hashlib.sha256(archive.read(name)).hexdigest()==h
    for group in ('without_battery','with_battery'):
     rows=[{k:float(v) for k,v in r.items()} for r in csv.DictReader(io.StringIO(archive.read(group+'.csv').decode()))]
     m=metrics(rows);assert m==p['result']['metrics'][group]
     # Independent AC and stored-energy integration from CSV values.
     ac=sum((r['pv_ac_kw']+r['discharge_kw']+r['grid_import_kw']-r['load_kw']+r['unserved_kw']-r['charge_kw']-r['grid_export_kw'])*r['dt_s']/3600 for r in rows)
     de=sum((.95*r['charge_kw']-r['discharge_kw']/.95)*r['dt_s']/3600 for r in rows)
     assert abs(ac)<1e-9 and abs(rows[-1]['battery_energy_kwh']-30-de)<1e-9
     assert all(.1<=r['soc']<=.9 for r in rows)
   results.append(p)
  assert results[0]['result']['numeric_sha256']==results[-1]['result']['numeric_sha256']
  for scenario,duration in [('cloud_pass',60),('export_cap',120)]:
   job=manager.start(scenario,duration);p=wait(manager,job['id'])
   assert p['meta']['status']=='completed' and len(p['series']['with_battery'])==duration*10+1

  assert c.get('/api/live/state').json()==initial,'batch mutated paused live model'
  # Running live state advances independently while a batch executes.
  c.post('/api/live/control',json={'action':'start','speed':1})
  live_before=c.get('/api/live/state').json();job=manager.start('load_step',30);p=wait(manager,job['id'])
  live_after=c.get('/api/live/state').json()
  assert live_after['run_id']==live_before['run_id'] and live_after['config']==live_before['config']
  assert live_after['seq']>live_before['seq'] and p['meta']['status']=='completed'
  c.post('/api/live/control',json={'action':'pause','speed':1})
  # Saved results can be re-opened; a modified CSV must invalidate access/export.
  id=results[0]['meta']['id'];other=BenchManager(manager.root)
  assert other.detail(id)['result']==results[0]['result']
  path=manager.root/id/'with_battery.csv';raw=path.read_bytes();path.write_bytes(raw+b'corrupt')
  assert c.get('/api/bench/jobs/'+id).status_code==409
  assert c.get('/api/bench/jobs/'+id+'/export').status_code==409
  path.write_bytes(raw)
  # Cross-manager worker lease and cooperative cancellation.
  job=manager.start('cloud_pass',120)
  try:other.start('load_step',30);raise AssertionError('concurrent manager accepted')
  except Busy:pass
  manager.cancel(job['id']);cancelled=wait(manager,job['id']);assert cancelled['meta']['status']=='cancelled'
  assert 'result' not in cancelled
  try:manager.export(job['id']);raise AssertionError('partial export accepted')
  except Busy:pass
  # Interrupted task recovery does not silently retry numerical work.
  abandoned=manager.root/'bench_0123456789abcdef';abandoned.mkdir();(abandoned/'meta.json').write_text(json.dumps({'id':abandoned.name,'status':'running','created_at_utc':'0'}))
  other.activate();assert json.loads((abandoned/'meta.json').read_text())['status']=='interrupted'
  for duration in (30,60,120):
   spec,inputs=protocol('load_step',duration);assert len(inputs)==duration*10
   assert sum(r['end_s']-r['start_s'] for r in inputs)>duration-1e-8
 print('PASS: 3 paired scenarios, deterministic repeat, independent energy integrals, ZIP integrity, live isolation, cancellation, cross-manager lease, persistence and tamper rejection')
if __name__=='__main__':run()
