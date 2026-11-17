"""Verify saved scene trace is the actual model state at each numerical step."""
import csv,hashlib,io,json,os,tempfile,time
from pathlib import Path
TMP=tempfile.TemporaryDirectory();os.environ['WG_RUNTIME_DIR']=TMP.name
from fastapi.testclient import TestClient
import server
from bench_experiments import BenchManager,scene_row,protocol
from live_simulation import Model,DEFAULT

def wait(c,id):
 until=time.monotonic()+45
 while time.monotonic()<until:
  p=c.get('/api/bench/jobs/'+id).json()
  if p['meta']['status'] not in ('queued','running','cancelling'):return p
  time.sleep(.02)
 raise AssertionError('timeout')
def run():
 with TestClient(server.app) as c:
  original=c.get('/api/live/state').json()
  for scenario in ('cloud_pass','load_step','export_cap'):
   m=c.post('/api/bench/jobs',json={'scenario':scenario,'duration_s':30}).json();id=m['id']
   assert c.get('/api/bench/jobs/'+id+'/scene').status_code==409
   detail=wait(c,id);assert detail['meta']['status']=='completed',detail
   response=c.get('/api/bench/jobs/'+id+'/scene');assert response.status_code==200,response.text
   data=response.json();assert data['id']==id and len(data['frames'])==301
   spec,inputs=protocol(scenario,30);model=Model(dict({k:v for k,v in inputs[0].items() if k in DEFAULT},battery_enabled=False))
   assert scene_row(model.last)==data['frames'][0]
   for i,row in enumerate(inputs,1):
    model.config=dict({k:v for k,v in row.items() if k in DEFAULT},battery_enabled=False);model.step(.1)
    assert scene_row(model.last)==data['frames'][i],(scenario,i)
    assert data['frames'][i]['seq']==detail['series']['with_battery'][i]['seq']
    for group in ('with_battery','without_battery'):
     r=detail['series'][group][i];f=data['frames'][i]
     assert abs(sum(f['dc_kw_'+str(z)] for z in range(6))-r['pv_dc_available_kw'])<1e-8
     assert abs(sum(f['temperature_C_'+str(z)] for z in range(6))/6-r['temperature_C'])<1e-8
   if scenario=='load_step':assert data['inputs'][99]['load_kw']==25 and data['inputs'][100]['load_kw']==90
   if scenario=='export_cap':assert data['inputs'][99]['grid_export_kw']==60 and data['inputs'][100]['grid_export_kw']==0
   assert c.get('/api/live/state').json()==original
   # Reopening does not recompute, even when the numerical Model is unavailable.
   import bench_experiments
   saved=bench_experiments.Model
   try:
    bench_experiments.Model=lambda *a,**k:(_ for _ in ()).throw(AssertionError('recomputed'))
    assert BenchManager(server.BENCH.root).scene(id)==data
   finally:bench_experiments.Model=saved
   path=server.BENCH.root/id/'scene.csv';raw=path.read_bytes();path.write_bytes(raw+b'edited')
   assert c.get('/api/bench/jobs/'+id+'/scene').status_code==409
   path.write_bytes(raw)
   # Legacy V16 results remain viewable/exportable; absence of a scene is explicit.
   meta_path=path.parent/'meta.json';meta=json.loads(meta_path.read_text());meta['artifact_sha256'].pop('scene.csv');meta_path.write_text(json.dumps(meta));server.BENCH.live_meta=None
   assert c.get('/api/bench/jobs/'+id).status_code==200
   missing=c.get('/api/bench/jobs/'+id+'/scene');assert missing.status_code==409 and '旧版实验' in missing.json()['detail']
   assert c.get('/api/bench/jobs/'+id+'/export').status_code==200
 print('PASS: all saved scene frames match independently stepped model; time/zone aggregates, event boundaries, source isolation, reopen without recomputation, tamper rejection, V16 compatibility')
if __name__=='__main__':run()
