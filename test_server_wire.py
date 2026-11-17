"""Actual localhost HTTP/WebSocket integration; no browser or recorded API adapter."""
import asyncio,hashlib,io,json,os,socket,subprocess,sys,tempfile,time,zipfile
from pathlib import Path
import httpx,websockets,inspect
connect=lambda url:websockets.connect(url,**({"proxy":None} if "proxy" in inspect.signature(websockets.connect).parameters else {}))
ROOT=Path(__file__).resolve().parent

async def wait_packet(ws,predicate):
 deadline=time.monotonic()+5
 while time.monotonic()<deadline:
  value=json.loads(await asyncio.wait_for(ws.recv(),timeout=3))
  if predicate(value):return value
 raise AssertionError('Expected packet did not arrive')

async def exercise(port):
 base=f'http://127.0.0.1:{port}';wsbase=f'ws://127.0.0.1:{port}'
 async with httpx.AsyncClient(base_url=base,timeout=10,trust_env=False) as c:
  initial=(await c.get('/api/live/state')).json()
  assert not initial['running'] and initial['seq']==0
  assert (await c.get('/platform')).status_code==200
  assert (await c.get('/experiments')).status_code==200
  started=(await c.post('/api/bench/jobs',json={'scenario':'load_step','duration_s':30})).json()
  for _ in range(200):
   experiment=(await c.get('/api/bench/jobs/'+started['id'])).json()
   if experiment['meta']['status']=='completed':break
   await asyncio.sleep(.05)
  assert experiment['meta']['status']=='completed'
  assert experiment['result']['validation']['passed']
  scene=(await c.get('/api/bench/jobs/'+started['id']+'/scene')).json()
  assert scene['id']==started['id'] and len(scene['frames'])==301
  diagnostic=(await c.get('/api/bench/jobs/'+started['id']+'/diagnostics')).json()
  assert diagnostic['id']==started['id'] and diagnostic['events']
  assert (await c.get('/api/bench/jobs/'+started['id']+'/diagnostics/export')).json()==diagnostic
  assert (await c.get('/api/bench/jobs/'+started['id']+'/export')).status_code==200
  assert (await c.get('/api/live/state')).json()==initial

  assert (await c.get('/matlab')).status_code==200
  env=(await c.get('/api/matlab/environment')).json()
  jobs_before=(await c.get('/api/matlab/jobs')).json()
  assert isinstance(env['executable_found'],bool)
  if not env['executable_found']:
   response=await c.post('/api/matlab/jobs',json={'scenario':'test','algorithm':'inc','client_token':'9'*32})
   assert response.status_code==503 and (await c.get('/api/matlab/jobs')).json()==jobs_before
  assert (await c.get('/comparison')).status_code==200
  pair=(await c.get('/api/comparison/info')).json();assert pair['samples']==80001
  stream=await c.get('/api/comparison/data');assert stream.status_code==200
  paired=stream.json();assert len(paired['po'])==len(paired['inc'])==80001
  assert (await c.get('/api/live/state')).json()==initial
  assert (await c.get('/integration')).status_code==200
  info=(await c.get('/api/integration/info')).json();assert info['matlab_online'] is False
  csv_raw=(ROOT/'static/integration/archive_signals.csv').read_bytes()
  source=json.loads((ROOT/'static/integration/archive_source.json').read_text())
  body={'title':'TCP离线接入验收','csv_text':csv_raw.decode('utf-8'),'manifest':source}
  assert (await c.post('/api/integration/preview',json=body)).status_code==200
  saved=await c.post('/api/integration/import',json=body);assert saved.status_code==200,saved.text
  rid=saved.json()['id'];read=(await c.get('/api/integration/records/'+rid)).json()
  assert len(read['rows'])==6001 and read['manifest']==source
  with zipfile.ZipFile(io.BytesIO((await c.get('/api/integration/records/'+rid+'/export')).content)) as z:
   assert z.read('signals.csv')==csv_raw
  assert (await c.get('/api/live/state')).json()==initial
  assert hashlib.sha256((await c.get(info['model']['glb_url'])).content).hexdigest()==info['model']['glb_sha256']
  assert (await c.get('/assets/usability.css')).status_code==200
  assert (await c.get('/api/health')).json()['status']=='ok'
  async with connect(wsbase+'/ws/telemetry') as replay:
   replay_first=await wait_packet(replay,lambda x:'sample' in x)
   assert replay_first['playing'] is False
   response=await c.post('/api/control',json={'action':'seek','value':1.25});assert response.status_code==200
   replay_at=await wait_packet(replay,lambda x:abs(x['cursor']-1.25)<1e-9)
   assert abs(replay_at['sample']['time_s']-1.25)<.00101
  async with connect(wsbase+'/ws/simulation') as ws:
   first=await wait_packet(ws,lambda x:'state' in x)
   assert not first['state']['running'] and first['state']['run_id']==initial['run_id']
   assert (await c.post('/api/live/control',json={'action':'start','speed':1})).status_code==200
   moving=await wait_packet(ws,lambda x:x['state']['seq']>=3)
   assert moving['state']['running'] and moving['rows']
   assert (await c.post('/api/live/control',json={'action':'pause','speed':1})).status_code==200
   paused=await wait_packet(ws,lambda x:x['state']['running'] is False)
   t=paused['state']['time_s'];await asyncio.sleep(.25)
   assert (await c.get('/api/live/state')).json()['time_s']==t
  # Removing a frontend connection must not stop the server-side numerical thread.
  await c.post('/api/live/control',json={'action':'start','speed':1});await asyncio.sleep(.25)
  after=(await c.get('/api/live/state')).json();assert after['time_s']>t
  async with connect(wsbase+'/ws/simulation') as reconnected:
   fresh=await wait_packet(reconnected,lambda x:x['state']['seq']>=after['seq'])
   assert fresh['state']['run_id']==initial['run_id']
  await c.post('/api/live/control',json={'action':'pause','speed':1})
  detail=(await c.get('/api/records/'+initial['run_id'])).json();assert detail['summary']['samples']>0
  response=await c.get('/api/records/'+initial['run_id']+'/export');assert response.status_code==200
  with zipfile.ZipFile(io.BytesIO(response.content)) as z:
   manifest=json.loads(z.read('manifest.json'))
   for f in manifest['files']:assert hashlib.sha256(z.read(f['file'])).hexdigest()==f['sha256']
  reset=(await c.post('/api/live/control',json={'action':'reset','speed':1})).json()
  assert reset['run_id']!=initial['run_id'] and not reset['running'] and reset['seq']==0
  assert (await c.get('/api/records/'+initial['run_id'])).json()['summary']['samples']>0
  assert (await c.post('/api/control',json={'action':'speed','value':3})).status_code==400
  out=ROOT/'frontend/.test-output';out.mkdir(exist_ok=True)
  (out/'wire_packets.json').write_text(json.dumps({'replay':replay_at,'live':moving}),encoding='utf-8')
 print('PASS: offline CSV preview/import/read/export + GLB HTTP hash and isolated live state; actual localhost HTTP and two WebSockets, paused startup, valid replay seek, numerical progression/pause, disconnect continuity, reconnect without reset, coherent record ZIP and historical run preservation.')

def main():
 with tempfile.TemporaryDirectory() as temp:
  with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
  env=dict(os.environ,WG_RUNTIME_DIR=temp,PYTHONUTF8='1')
  process=subprocess.Popen([sys.executable,'-m','uvicorn','server:app','--host','127.0.0.1','--port',str(port),'--log-level','warning'],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
  try:
   ready=False
   for _ in range(100):
    if process.poll() is not None:raise RuntimeError(process.communicate()[0])
    try:
     if httpx.get(f'http://127.0.0.1:{port}/api/health',timeout=.3,trust_env=False).status_code==200:ready=True;break
    except httpx.RequestError:pass
    time.sleep(.05)
   assert ready,'Server did not become ready'
   asyncio.run(exercise(port))
  finally:
   if process.poll() is None:process.terminate()
   try:logs=process.communicate(timeout=8)[0]
   except subprocess.TimeoutExpired:process.kill();logs=process.communicate()[0]
   if process.returncode not in (0,-15):print(logs[-2500:])

if __name__=='__main__':main()
