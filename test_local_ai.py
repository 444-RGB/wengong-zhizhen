from unittest import mock
"""Protocol/lifecycle tests use a clearly synthetic Ollama fixture, never actual inference."""
import json,os,tempfile,time,threading,uuid,zipfile,hashlib,struct,unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
os.environ['WG_RUNTIME_DIR']=tempfile.mkdtemp(prefix='wg-v24-api-')
from fastapi.testclient import TestClient
from fastapi import FastAPI,WebSocket
from local_ai import LocalAI,Ollama,summarize,MODELS
from local_access import LocalAccess
ROOT=Path(__file__).resolve().parent
def peer_app(app,ip):
 async def wrapped(scope,receive,send):
  scope=dict(scope);scope['client']=(ip,1000);await app(scope,receive,send)
 return wrapped
ROWS=[{'time_s':0,'pv_ac_kw':10},{'time_s':36,'pv_ac_kw':20}]
def snapshot(*args):return summarize(ROWS,'test_fixture','fixture','test-run',{'pv_ac_kw':('测试功率','kW',1)})
NARRATIVE={'summary':'协议测试夹具，不是DeepSeek推理。','findings':[{'text':'仅测试证据引用','evidence_ids':['pv_ac_kw_max']}],'suggestions':[]}
class Fixture(BaseHTTPRequestHandler):
 mode='ok';calls=[]
 def log_message(self,*args):pass
 def do_GET(self):self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'models':[{'name':MODELS[0]}]}).encode())
 def do_POST(self):
  p=json.loads(self.rfile.read(int(self.headers['Content-Length'])));self.calls.append((self.path,p));self.send_response(200);self.end_headers()
  if self.path=='/api/show':self.wfile.write(b'{"thinking":{"values":[false,true],"default":true}}');return
  if self.path=='/api/pull':
   self.wfile.write(b'{"status":"pulling","completed":1,"total":2}\n');
   if self.mode!='missing_success':self.wfile.write(b'{"status":"success"}\n')
  else:
   data=json.loads(json.dumps(NARRATIVE))
   if self.mode=='bad_evidence':data['findings'][0]['evidence_ids']=['invented']
   content='not JSON' if self.mode=='bad_json' else json.dumps(data)
   self.wfile.write(json.dumps({'model':p['model'],'done':True,'done_reason':'length' if self.mode=='truncated' else 'stop','message':{'content':content}}).encode())
class AIProtocol(unittest.TestCase):
 @classmethod
 def setUpClass(c):
  c.http=ThreadingHTTPServer(('127.0.0.1',0),Fixture);threading.Thread(target=c.http.serve_forever,daemon=True).start();c.client=Ollama(tempfile.mkdtemp(),f'http://127.0.0.1:{c.http.server_port}')
 @classmethod
 def tearDownClass(c):c.http.shutdown();c.http.server_close()
 def setUp(self):Fixture.mode='ok'
 def test_protocol(self):
  self.assertEqual(self.client.status()['models'],[MODELS[0]]);self.assertEqual(self.client.chat(MODELS[0],snapshot()),NARRATIVE)
  p=Fixture.calls[-1][1];self.assertFalse(p['stream']);self.assertIn('format',p);self.assertFalse(p['think'])
  for mode in ['bad_evidence','bad_json','truncated']:
   Fixture.mode=mode
   with self.assertRaises(ValueError):self.client.chat(MODELS[0],snapshot())
 def test_pull_requires_confirmation(self):
  from unittest.mock import patch
  states=iter([{'models':[]},{'models':[MODELS[0]]}]);events=[]
  with patch.object(self.client,'ensure'),patch.object(self.client,'status',side_effect=lambda:next(states)):
   self.client.pull(MODELS[0],events.append,threading.Event())
  self.assertEqual(events[-1]['status'],'success');Fixture.mode='missing_success'
  with patch.object(self.client,'ensure'),patch.object(self.client,'status',return_value={'models':[]}):
   with self.assertRaises(RuntimeError):self.client.pull(MODELS[0],events.append,threading.Event())
  with patch.object(self.client,'ensure'),patch.object(self.client,'status',return_value={'models':[MODELS[0]]}),patch.object(self.client,'_request',side_effect=AssertionError('Cached model must work offline')):
   self.client.pull(MODELS[0],events.append,threading.Event())
 def test_evidence(self):
  s=snapshot();m={x['id']:x['value'] for x in s['metrics']};self.assertEqual(m['energy_wh'],150);self.assertEqual(m['pv_ac_kw_change'],10);self.assertEqual(snapshot()['snapshot_sha256'],s['snapshot_sha256'])
  with self.assertRaises(ValueError):summarize([{'time_s':0,'a':0},{'time_s':1,'a':float('nan')},{'time_s':2,'a':2}],'fixture','x','r',{'a':('a','',1)})
  with self.assertRaises(ValueError):summarize(ROWS[::-1],'fixture','x','r',{'pv_ac_kw':('a','',1)})
 def test_jobs(self):
  class Slow:
   models=[MODELS[0]]
   def status(self):return {'models':self.models}
   def chat(self,*args):release.wait(2);return NARRATIVE
  release=threading.Event();root=Path(tempfile.mkdtemp());a=LocalAI(root,snapshot,Slow());a.start();token=uuid.uuid4().hex;j=a.create('analysis',MODELS[0],token)
  self.assertEqual(a.create('analysis',MODELS[0],token)['id'],j['id'])
  with self.assertRaises(RuntimeError):a.create('analysis',MODELS[0],uuid.uuid4().hex)
  with self.assertRaises(ValueError):a.create('prepare',MODELS[0],token)
  a.close();a.start();self.assertEqual(a.read(j['id'])['status'],'interrupted');release.set();time.sleep(.05);self.assertEqual(a.read(j['id'])['status'],'interrupted')
  j=a.create('analysis',MODELS[0],uuid.uuid4().hex)
  for _ in range(100):
   if a.read(j['id'])['status']=='completed':break
   time.sleep(.01)
  self.assertEqual(a.read(j['id'])['result'],NARRATIVE);a.close();a.start();self.assertEqual(a.read(j['id'])['status'],'completed')
  a.client.models=[];j=a.create('analysis',MODELS[0],uuid.uuid4().hex)
  for _ in range(100):
   if a.read(j['id'])['status']=='failed':break
   time.sleep(.01)
  self.assertEqual(a.read(j['id'])['status'],'failed');self.assertIsNone(a.read(j['id'])['result']);self.assertEqual(a.read(j['id'])['snapshot']['samples'],2);a.close()
 def test_source_routes(self):
  import server
  with TestClient(server.app,base_url='http://127.0.0.1') as c:
   before=c.get('/api/live/state').json();self.assertEqual(c.get('/ai').status_code,200)
   for source in ['live','po','inc']:
    r=c.get('/api/ai/snapshot',params={'source':source});self.assertEqual(r.status_code,200,r.text);s=r.json();self.assertEqual(s['samples'],1 if source=='live' else 80001)
   self.assertEqual(c.get('/api/live/state').json(),before);self.assertFalse(c.get('/api/launch/info').json()['read_only'])
   self.assertEqual(c.post('/api/ai/jobs',json={'client_token':'x'}).status_code,422)
   self.assertEqual(c.post('/api/ai/jobs',headers={'Origin':'https://other.example'},json={'client_token':'a'*32}).status_code,403)
   self.assertEqual(c.get('/api/ai/jobs/no_such').status_code,404)
   # V26 explicitly retires local-model creation; archived GET evidence stays readable.
   with mock.patch.object(server.AI,'create') as create:
    self.assertEqual(c.post('/api/ai/jobs',json={'client_token':uuid.uuid4().hex}).status_code,410)
    create.assert_not_called()
   self.assertEqual(c.get('/api/live/state').json(),before)

class Access(unittest.TestCase):
 def test_read_only(self):
  app=FastAPI();settings={'lan':True,'token':'secret'};app.add_middleware(LocalAccess,settings=lambda:settings)
  @app.get('/')
  def page():return {'ok':True}
  @app.post('/')
  def change():return {'changed':True}
  @app.websocket('/ws')
  async def ws(w:WebSocket):await w.accept();await w.send_json({'ok':True});await w.close()
  with TestClient(peer_app(app,'192.168.2.30')) as c:
   self.assertEqual(c.get('/').status_code,403);self.assertEqual(c.get('/?access=错误').status_code,403)
   r=c.get('/?access=secret',follow_redirects=False);self.assertEqual(r.status_code,303);self.assertIn('HttpOnly',r.headers['set-cookie']);self.assertNotIn('access',r.headers['location'])
   self.assertEqual(c.get('/').status_code,200);self.assertEqual(c.post('/').status_code,403)
   with c.websocket_connect('/ws') as ws:self.assertTrue(ws.receive_json()['ok'])
   settings['lan']=False;self.assertEqual(c.get('/').status_code,403)
  settings['lan']=True
  with TestClient(peer_app(app,'8.8.8.8')) as c:self.assertEqual(c.get('/?access=secret').status_code,403)
  with TestClient(peer_app(app,'127.0.0.1')) as c:self.assertEqual(c.post('/').status_code,200);self.assertEqual(c.post('/',headers={'Origin':'https://other.example'}).status_code,403)
class Portable(unittest.TestCase):
 @unittest.skipUnless((ROOT/'portable/SHA256.json').exists(),'Source archive excludes Windows binaries')
 def test_bundle_integrity(self):
  base=ROOT/'portable';manifest=json.loads((base/'SHA256.json').read_text())
  for n,h in manifest.items():self.assertEqual(hashlib.sha256((base/n).read_bytes()).hexdigest(),h)
  with zipfile.ZipFile(base/'python-3.13.17-embed-amd64.zip') as z:
   self.assertIsNone(z.testzip());exe=z.read('python.exe');offset=struct.unpack_from('<I',exe,0x3c)[0];self.assertEqual(exe[offset:offset+4],b'PE\0\0');self.assertEqual(struct.unpack_from('<H',exe,offset+4)[0],0x8664);self.assertIn('python313._pth',z.namelist())
  for p in (base/'wheels').glob('*.whl'):
   with zipfile.ZipFile(p) as z:self.assertIsNone(z.testzip())
  bat=(ROOT/'start_windows.bat').read_bytes();self.assertIn(b'\r\n',bat);self.assertNotIn(b'\\r\\n',bat);self.assertNotIn(b'goto legacy',bat);self.assertNotIn(b'ExecutionPolicy',bat);self.assertIn(b'python313._pth',bat)
  import bootstrap;bootstrap.preflight()
if __name__=='__main__':unittest.main(verbosity=2)
