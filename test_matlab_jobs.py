"""Real subprocess/HTTP tests with explicit Python fixture. Never runs MATLAB."""
import os,tempfile,sys,time,json,hashlib,zipfile,subprocess
from pathlib import Path
TMP=tempfile.TemporaryDirectory();os.environ['WG_RUNTIME_DIR']=TMP.name
from fastapi.testclient import TestClient
import server
from matlab_jobs import MatlabJobs,TERMINAL,batch_command
from simulink_bridge import Bridge
ROOT=Path(__file__).resolve().parent

def wait(manager,id):
 until=time.monotonic()+15
 while time.monotonic()<until:
  d=manager.detail(id)
  if d['status'] in TERMINAL:return d
  time.sleep(.03)
 raise AssertionError('fixture task did not finish')
def manager(path,mode='success',timeout=20):
 return MatlabJobs(path,ROOT/'matlab_adapter',Bridge(Path(path).parent/'records'),executable_provider=lambda:sys.executable,command_factory=lambda w,d,j:[sys.executable,str(ROOT/'tests_matlab_fixture.py'),mode,str(ROOT)],timeout_s=timeout)
def run():
 command=batch_command('C:/MATLAB/bin/matlab.exe',ROOT/"中文 path with ' quote",windows=True)
 assert command[1]=='-wait' and command[-2:]==['-batch','wg_task_entry'] and command[3].endswith("中文 path with ' quote")
 hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'matlab_adapter/project').iterdir() if p.is_file()}
 with tempfile.TemporaryDirectory() as td:
  m=manager(Path(td)/'jobs');m.start();other=manager(Path(td)/'jobs');other.start();assert not other.active;other.stop()
  first=m.create('test','po','a'*32);assert m.create('test','po','a'*32)['id']==first['id']
  second=m.create('test','inc','b'*32);m.cancel(second['id']);assert m.detail(second['id'])['status']=='cancelled'
  done=wait(m,first['id']);assert done['status']=='completed',done
  assert m.bridge.read(done['record_id'])['manifest']['test_fixture'].startswith('reused')
  another=m.create('test','po','c'*32);assert wait(m,another['id'])['record_id']==done['record_id']
  with zipfile.ZipFile(m.export(first['id'])) as z:
   assert 'executor.log' in z.namelist() and not any(n.endswith('capture.mat') for n in z.namelist())
   for n,h in json.loads(z.read('hashes.json')).items():assert hashlib.sha256(z.read(n)).hexdigest()==h
  fourth=m.create('test','po','2'*32);fifth=m.create('test','inc','3'*32)
  a=wait(m,fourth['id']);b=wait(m,fifth['id']);assert a['status']==b['status']=='completed'
  assert b['started_at']>=a['finished_at'],'MATLAB tasks ran concurrently'
  m.stop();m2=manager(Path(td)/'jobs');m2.start();assert m2.detail(first['id'])['status']=='completed';m2.stop()
 for mode,expected in [('captured','captured'),('bad','failed'),('fail','failed'),('hang','failed')]:
  with tempfile.TemporaryDirectory() as td:
   m=manager(Path(td)/'jobs',mode,timeout=.3 if mode=='hang' else 20);m.start();j=m.create('test','po','d'*32);done=wait(m,j['id']);assert done['status']==expected,done;assert not done['record_id'];m.stop()
 with tempfile.TemporaryDirectory() as td:
  m=manager(Path(td)/'jobs','hang');m.start();j=m.create('test','po','e'*32)
  until=time.monotonic()+5
  while m.detail(j['id'])['status']!='running' and time.monotonic()<until:time.sleep(.02)
  m.stop();assert m.detail(j['id'])['status']=='interrupted'
 with tempfile.TemporaryDirectory() as td:
  m=manager(Path(td)/'jobs');m.root.mkdir(exist_ok=True);id='mat_'+'f'*32;d=m.root/id;d.mkdir();(d/'job.json').write_text(json.dumps({'id':id,'status':'running','client_token':'f'*32,'scenario':'test','algorithm':'po','created_at':'2026-01-01T00:00:00+00:00'}));m.start();assert m.detail(id)['status']=='interrupted';m.stop()
 # Exercise the actual FastAPI routes, including same-origin/loopback gates.
 with tempfile.TemporaryDirectory() as td:
  original=server.MATLAB_JOBS;server.MATLAB_JOBS=manager(Path(td)/'jobs');server.MATLAB_JOBS.bridge=server.SIM_BRIDGE
  try:
   with TestClient(server.app,base_url='http://127.0.0.1') as c:
    initial=c.get('/api/live/state').json();assert c.get('/api/matlab/environment').json()['ready']
    assert c.get('/api/matlab/environment',headers={'host':'evil.test'}).status_code==403
    body={'scenario':'test','algorithm':'inc','client_token':'1'*32}
    assert c.post('/api/matlab/jobs',json=body,headers={'Origin':'http://evil.test'}).status_code==403
    assert c.post('/api/matlab/jobs',json={**body,'command':'anything'}).status_code==422
    response=c.post('/api/matlab/jobs',json=body,headers={'Origin':'http://127.0.0.1'});assert response.status_code==202,response.text
    id=response.json()['id'];done=wait(server.MATLAB_JOBS,id);assert done['status']=='completed',done
    assert c.get('/api/matlab/jobs/'+id).json()['record_id']==done['record_id']
    assert c.get('/api/matlab/jobs/'+id+'/export').status_code==200
    saved=c.get('/api/integration/records/'+done['record_id']);assert saved.status_code==200 and len(saved.json()['rows'])==80001
    assert c.get('/api/integration/records/'+done['record_id']+'/export').status_code==200
    assert c.get('/matlab').status_code==200 and c.get('/api/live/state').json()==initial
  finally:server.MATLAB_JOBS=original
 assert all(hashlib.sha256(p.read_bytes()).hexdigest()==h for p,h in hashes.items())
 print('PASS: Python subprocess fixtures ONLY; single worker/file lock,idempotent submission,queued cancellation,persistence/recovery,timeout/shutdown,source rejection,export,HTTP origin gates; source files unchanged. MATLAB NOT executed.')
if __name__=='__main__':run()
