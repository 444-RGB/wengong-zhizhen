"""Cloud transport fixtures only. No real API key, paid request, or model claim."""
from pathlib import Path
from unittest import mock
import unittest,tempfile,io,json,time,uuid,os,urllib.error,threading
from deepseek_api import DeepSeekAssistant,Knowledge,ENDPOINT
ROOT=Path(__file__).resolve().parent
KEY='sk-'+('fixture-only-'*3)
def uid():return uuid.uuid4().hex
def done(a,id):
 end=time.monotonic()+4
 while time.monotonic()<end:
  j=a.read(id)
  if j['status']!='running':return j
  time.sleep(.01)
 raise AssertionError('Fixture timeout')
def stream(text='测试回答 [snapshot]',finish='stop',complete=True):
 parts=[{'choices':[{'delta':{'content':text[:4]},'finish_reason':None}]},{'choices':[{'delta':{'content':text[4:]},'finish_reason':finish}],'usage':{'total_tokens':42}}]
 return io.BytesIO((''.join('data: '+json.dumps(x,ensure_ascii=False)+'\n\n' for x in parts)+('data: [DONE]\n\n' if complete else '')).encode())
class Cloud(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.n=0;self.requests=[]
  def context(source):self.n+=1;return {'source':source,'captured_at_utc':'fixture','metrics':[{'id':'Pgrid_W_last','value':self.n,'unit':'kW'}]}
  def open_(req,timeout):self.requests.append(req);return stream()
  self.a=DeepSeekAssistant(ROOT,self.tmp.name,context,open_)
 def tearDown(self):self.tmp.cleanup()
 def create(self,**kw):return self.a.create(kw.get('q','现在功率？'),kw.get('source','live'),kw.get('session',uid()),kw.get('token',uid()),'/ai')
 def test_key_missing_and_secret_is_private(self):
  with self.assertRaises(ValueError):self.create()
  self.a.configure(KEY,'deepseek-flash');s=self.a.status();self.assertTrue(s['configured']);self.assertNotIn(KEY,json.dumps(s));self.assertNotIn('key',s)
  self.a.configure(None,'deepseek-v4-pro');self.assertEqual(self.a.settings()['key'],KEY)
  self.a.configure('','deepseek-flash');self.assertFalse(self.a.status()['configured'])
 def test_stream_context_fresh_and_idempotent(self):
  self.a.configure(KEY,'deepseek-flash');token=uid();session=uid();j=self.create(token=token,session=session);r=done(self.a,j['id']);self.assertEqual(r['status'],'completed');self.assertEqual(r['answer'],'测试回答 [snapshot]');self.assertEqual(r['usage']['total_tokens'],42)
  same=self.create(token=token,session=session);self.assertEqual(same['id'],j['id']);self.assertEqual(len(self.requests),1);self.assertEqual(len(self.a.conversation(session)),1)
  with self.assertRaises(ValueError):self.create(token=token,session=session,q='不同的问题')
  second=done(self.a,self.create(session=session)['id']);self.assertEqual(second['evidence']['metrics'][0]['value'],2)
  payload=json.loads(self.requests[-1].data);self.assertEqual(len(payload['messages']),4);self.assertTrue(payload['stream']);self.assertNotIn('tools',payload);self.assertEqual(self.requests[-1].full_url,ENDPOINT);self.assertNotIn(KEY,json.dumps(second))
 def test_partial_or_failed_never_complete(self):
  self.a.configure(KEY,'deepseek-flash');self.a.opener=lambda *a,**kw:stream(finish='length');self.assertEqual(done(self.a,self.create()['id'])['status'],'failed')
  self.a.opener=lambda *a,**kw:stream(complete=False);self.assertEqual(done(self.a,self.create()['id'])['status'],'failed')
  def fail(*a,**kw):raise urllib.error.HTTPError(ENDPOINT,401,KEY,{},None)
  self.a.opener=fail;r=done(self.a,self.create()['id']);self.assertEqual(r['status'],'failed');self.assertNotIn(KEY,json.dumps(r));self.assertIn('Key',r['error'])
 def test_knowledge_and_busy_guard(self):
  docs=Knowledge(ROOT).search('Simulink 调节 辐照 参数');self.assertTrue(docs);self.assertTrue(any('辐照' in d['text'] for d in docs));self.assertTrue(all('script' not in d['text'][:10] for d in docs))
  self.a.configure(KEY,'deepseek-flash');gate=threading.Event()
  def slow(*a,**kw):gate.wait(2);return stream()
  self.a.opener=slow;j=self.create()
  try:
   with self.assertRaises(RuntimeError):self.create()
   with self.assertRaises(RuntimeError):self.a.configure(KEY,'deepseek-flash')
  finally:gate.set();done(self.a,j['id'])
 def test_setpoint_command_applies_in_python_without_cloud(self):
  os.environ.setdefault('WG_RUNTIME_DIR',tempfile.mkdtemp(prefix='wg-cloud-http-'))
  from fastapi.testclient import TestClient
  import server
  from live_simulation import Runtime
  live=Runtime(Path(self.tmp.name)/'live')
  assistant=DeepSeekAssistant(ROOT,self.tmp.name,server.assistant_context,self.a.opener,server.assistant_control)
  def ask(c,question,source='auto'):
   return c.post('/api/assistant/messages',json={'question':question,'source':source,
      'session_id':uid(),'client_token':uid(),'page':'/platform?page=live'})
  try:
   with mock.patch.object(server,'LIVE',live),mock.patch.object(server,'ASSISTANT',assistant),TestClient(server.app,base_url='http://127.0.0.1') as c:
    reply=ask(c,'把组件温度设为 65℃，风速调到 6 m/s，湿度设为 45%，沙尘光学厚度设为 0.3，积灰改成 20%')
    self.assertEqual(reply.status_code,202,reply.text)
    self.assertIn('已提交',reply.json()['answer'])
    self.assertEqual(len(self.requests),0)
    self.assertFalse(live.running)
    frame=live.model.step()
    self.assertEqual(frame['temperature_C'],65)
    self.assertEqual(frame['config']['humidity'],45)
    old=dict(live.model.config)
    bad=ask(c,'把辐照设为 200 W/m²，湿度设为 150%')
    self.assertIn('湿度',bad.json()['answer'])
    self.assertEqual(live.model.config,old)
    self.assertIn('只能查看',ask(c,'把辐照设为 200','po').json()['answer'])
    self.assertEqual(live.model.config,old)
    ask(c,'把辐照调到 200 会怎样？')
    self.assertEqual(live.model.config,old)
  finally:
   live.close()
 def test_http_routes_no_model_control(self):
  os.environ.setdefault('WG_RUNTIME_DIR',tempfile.mkdtemp(prefix='wg-cloud-http-'))
  from fastapi.testclient import TestClient
  import server
  with mock.patch.object(server,'ASSISTANT',self.a),TestClient(server.app,base_url='http://127.0.0.1') as c:
   self.assertEqual(c.get('/ai').status_code,200);s=c.get('/api/assistant/context?source=live').json();self.assertTrue(s['available']);self.assertIn('Python',s['display_label'])
   native=c.get('/api/assistant/context?source=matlab_live').json();self.assertFalse(native['available'])
   self.assertEqual(c.post('/api/assistant/settings',headers={'Origin':'https://outside.example'},json={'api_key':KEY}).status_code,403)
   self.assertEqual(c.post('/api/assistant/settings',json={'api_key':KEY}).status_code,200)
   original=server.MATLIVE.snapshot();body={'question':'如何调参？','session_id':uid(),'client_token':uid(),'source':'live'}
   r=c.post('/api/assistant/messages',json=body);self.assertEqual(r.status_code,202,r.text);done(self.a,r.json()['id']);self.assertEqual(server.MATLIVE.snapshot(),original);self.assertEqual(len(c.get('/api/assistant/conversations/'+body['session_id']).json()),1);self.assertEqual(c.post('/api/ai/jobs',json={'client_token':uid()}).status_code,410)
   self.assertEqual(c.post('/api/assistant/messages',json=dict(body,source='invalid')).status_code,422)
   self.assertEqual(c.post('/api/assistant/messages',json=dict(body,question='   ')).status_code,422)
   self.assertNotIn(KEY,c.get('/api/assistant/status').text)
if __name__=='__main__':
 with mock.patch.dict(os.environ,{'DEEPSEEK_API_KEY':''}):unittest.main(verbosity=2)
