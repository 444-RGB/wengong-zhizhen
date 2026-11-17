"""Validate presentation integration and downloadable native assets; no MATLAB execution."""
from pathlib import Path
import os,tempfile,hashlib,zipfile,io,json
os.environ['WG_RUNTIME_DIR']=tempfile.mkdtemp()
from fastapi.testclient import TestClient
import server
ROOT=Path(__file__).resolve().parent
with TestClient(server.app,base_url='http://127.0.0.1') as c:
 for route in ['/','/overview','/story','/integration','/comparison','/matlab','/platform','/experiments']:
  r=c.get(route);assert r.status_code==200,route
 assert '/assets/entry.js' in c.get('/').text and '/assets/home.js' in c.get('/overview').text and '/assets/entry.js' in c.get('/story').text
 p=c.get('/api/showcase').json();assert len(p['runs'])==2
 for run in p['runs']:
  assert run['status']=='completed' and run['samples']==80001 and run['duration_s']==4
  b=c.get(run['download_url']).content;assert hashlib.sha256(b).hexdigest()==run['evidence_sha256']
  with zipfile.ZipFile(io.BytesIO(b)) as z:
   assert z.testzip() is None
   for n,h in json.loads(z.read('hashes.json')).items():assert hashlib.sha256(z.read(n)).hexdigest()==h
   j=json.loads(z.read('job.json'));assert j['id']==run['task_id'] and j['record_id']==run['record_id']
 for algorithm in ['po','inc']:
  r=c.get('/api/integration/presets/'+algorithm);assert r.status_code==200
  p=r.json();assert len(p['rows'])==80001 and p['summary']['duration_s']==4
  with zipfile.ZipFile(io.BytesIO(c.get('/api/integration/presets/'+algorithm+'/export').content)) as z:
   assert z.read('signals.csv')==(ROOT/'data/simulink_comparison'/algorithm/'signals.csv').read_bytes()
 assert c.get('/api/integration/presets/bad').status_code==404
 r=c.get('/api/resources/package');assert r.status_code==200
 with zipfile.ZipFile(io.BytesIO(r.content)) as z:
  assert z.testzip() is None
  hashes=json.loads(z.read('SHA256.json'))
  for n,h in hashes.items():assert hashlib.sha256(z.read(n)).hexdigest()==h
  assert z.read('software_models/generated/WG_refined_station.blend')[:7]==b'BLENDER'
  assert z.read('static/integration/WG_refined_station.glb')[:4]==b'glTF'
  assert z.read('matlab_adapter/project/WG_PV_Environment.slx')==(ROOT/'matlab_adapter/project/WG_PV_Environment.slx').read_bytes()
 for route in ['/api/resources/not_a_file','/api/resources/server.py']:assert c.get(route).status_code==404
 assert c.get('/api/resources/blend').content==(ROOT/'software_models/generated/WG_refined_station.blend').read_bytes()
print('PASS: actual asset downloads, model pack CRC/hash/native signatures, original SLX bytes, actual completed task evidence, read-only home/story and retained routes.')
