"""Protocol/lifecycle and real HTTP route tests using explicitly labelled child fixtures.
No MATLAB or physical-model output is fabricated as real validation.
"""
from pathlib import Path
from unittest import mock
import os,tempfile,time,sys,json,uuid,hashlib,io,zipfile,unittest
os.environ['WG_RUNTIME_DIR']=tempfile.mkdtemp(prefix='wg-ml-test-')
from matlab_live import MatlabLive,config
ROOT=Path(__file__).resolve().parent
CFG={'irradiance':1000,'iq_ref_pu':0.,'cloud':False}
def wait(fn,timeout=5):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  if fn():return
  time.sleep(.02)
 raise AssertionError('Timed out waiting for fixture lifecycle')
def factory(exe,workspace,folder):return [sys.executable,str(ROOT/'test_matlab_live_fixture.py')]
class Live(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.m=MatlabLive(Path(self.tmp.name),ROOT/'matlab_adapter',lambda:sys.executable,factory,timeout=.7);self.before=hashlib.sha256((ROOT/'matlab_adapter/project/WG_PV_Environment.slx').read_bytes()).hexdigest()
 def tearDown(self):self.m.close();self.tmp.cleanup();self.assertEqual(hashlib.sha256((ROOT/'matlab_adapter/project/WG_PV_Environment.slx').read_bytes()).hexdigest(),self.before)
 def create(self):
  self.id=self.m.create('po',CFG,uuid.uuid4().hex);wait(lambda:self.m.snapshot()['phase']=='paused');return self.id
 def test_steps_changes_and_evidence(self):
  token=uuid.uuid4().hex;id=self.m.create('po',CFG,token);self.assertEqual(self.m.create('po',CFG,token),id);wait(lambda:self.m.snapshot()['phase']=='paused');self.assertFalse(self.m.snapshot()['matlab_execution_confirmed'])
  self.m.command('step');wait(lambda:self.m.snapshot()['sample_count']==2000);first=self.m.snapshot();self.assertEqual(first['latest']['G_Wm2'],1000)
  self.m.configure({'irradiance':600,'iq_ref_pu':.1,'cloud':False});self.assertGreater(self.m.snapshot()['revision'],self.m.snapshot()['applied_revision']);self.m.command('step');wait(lambda:self.m.snapshot()['sample_count']==4000)
  second=self.m.snapshot();self.assertGreater(second['latest']['time_s'],first['latest']['time_s']);self.assertEqual(second['latest']['G_Wm2'],600);self.assertEqual(second['latest']['Pgrid_W'],2600);self.assertEqual(second['applied_revision'],second['revision']);self.assertEqual(len(self.m.data()['rows']),4000)
  self.assertEqual(self.m.data(first['latest']['time_s'],id)['rows'][0]['time_s'],.1)
  self.assertEqual(self.m.data(5,'previous')['rows'][0]['time_s'],0)
  self.m.command('start');wait(lambda:self.m.snapshot()['sample_count']>=6000);self.m.command('pause');wait(lambda:self.m.snapshot()['phase']=='paused');n=self.m.snapshot()['sample_count'];time.sleep(.4);self.assertEqual(self.m.snapshot()['sample_count'],n)
  self.m.command('stop');wait(lambda:not self.m.busy());self.assertEqual(self.m.snapshot()['phase'],'stopped')
  with zipfile.ZipFile(io.BytesIO(self.m.export(id))) as z:
   self.assertIsNone(z.testzip())
   self.assertNotIn('command.json',z.namelist())
   commands=[json.loads(z.read(name)) for name in sorted(z.namelist()) if name.startswith('command_')]
   self.assertEqual([command['seq'] for command in commands],list(range(1,len(commands)+1)))
   self.assertEqual([command['irradiance'] for command in commands[:2]],[1000,600])
   self.assertEqual(commands[-1]['action'],'stop')
   for name,h in json.loads(z.read('SHA256.json')).items():self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),h)
 def test_validation(self):
  for bad in [dict(CFG,irradiance=float('nan')),dict(CFG,iq_ref_pu=5),dict(CFG,cloud='yes'),dict(CFG,extra=0)]:
   with self.assertRaises(ValueError):config(bad)
  mismatch=MatlabLive(Path(self.tmp.name)/'mismatch',Path(self.tmp.name),lambda:sys.executable)
  self.assertFalse(mismatch.environment()['bridge_ready'])
  with self.assertRaisesRegex(RuntimeError,'版本不一致'):mismatch.create('po',CFG,uuid.uuid4().hex)
  m=MatlabLive(Path(self.tmp.name)/'missing',ROOT/'matlab_adapter',lambda:None)
  with self.assertRaises(RuntimeError):m.create('po',CFG,uuid.uuid4().hex)
 def test_invalid_reply_rejected(self):
  with mock.patch.dict(os.environ,{'WG_LIVE_FIXTURE_MODE':'wrong_sequence'}):
   self.create();self.m.command('step');wait(lambda:self.m.snapshot()['phase']=='failed');self.assertEqual(self.m.snapshot()['sample_count'],0)
 def test_timeout_no_fake_output(self):
  with mock.patch.dict(os.environ,{'WG_LIVE_FIXTURE_MODE':'timeout'}):
   self.create();self.m.command('step');wait(lambda:self.m.snapshot()['phase']=='failed');self.assertIsNone(self.m.snapshot()['latest'])
 def test_latest_parameter_revision_wins(self):
  with mock.patch.dict(os.environ,{'WG_LIVE_FIXTURE_MODE':'slow'}):
   self.create();self.m.command('step');wait(lambda:self.m.snapshot()['phase']=='computing');time.sleep(.05);self.m.configure(dict(CFG,irradiance=500));self.m.configure(dict(CFG,irradiance=700));wait(lambda:self.m.snapshot()['sample_count']==2000);self.assertEqual(self.m.snapshot()['latest']['G_Wm2'],1000);self.assertGreater(self.m.snapshot()['revision'],self.m.snapshot()['applied_revision']);self.m.command('step');wait(lambda:self.m.snapshot()['sample_count']==4000);self.assertEqual(self.m.snapshot()['latest']['G_Wm2'],700)
 def test_irradiance_transition_preserves_boundary_sample(self):
  with mock.patch.dict(os.environ,{'WG_LIVE_FIXTURE_MODE':'sample_boundary'}):
   self.create()
   self.m.configure(dict(CFG,irradiance=600))
   self.m.command('step')
   wait(lambda:self.m.snapshot()['sample_count']==2000)
   self.assertAlmostEqual(self.m.snapshot()['applied_time_s'],50e-6)
   self.assertEqual(self.m.rows[0]['G_Wm2'],1000)
   self.assertAlmostEqual(self.m.rows[1]['G_Wm2'],600,places=4)

 def test_stale_irradiance_still_fails(self):
  with mock.patch.dict(os.environ,{'WG_LIVE_FIXTURE_MODE':'stale_irradiance'}):
   self.create()
   self.m.configure(dict(CFG,irradiance=600))
   self.m.command('step')
   wait(lambda:self.m.snapshot()['phase']=='failed')
   self.assertEqual(self.m.snapshot()['sample_count'],0)

 def test_exit_status_reports_reason(self):
  for mode,phase,message in (
   ('idle_close','stopped','空闲超时'),
   ('exit_error','failed','WG:FixtureFailure：fixture MATLAB error'),
   ('exit_unreported','failed','退出码 31'),
  ):
   with self.subTest(mode=mode),mock.patch.dict(os.environ,{'WG_LIVE_FIXTURE_MODE':mode}):
    self.m.create('po',CFG,uuid.uuid4().hex)
    wait(lambda:not self.m.busy())
    self.assertEqual(self.m.snapshot()['phase'],phase)
    self.assertIn(message,self.m.snapshot()['message'])

 def test_http_source_isolation(self):
  from fastapi.testclient import TestClient
  import server
  original=server.MATLIVE
  with TestClient(server.app,base_url='http://127.0.0.1') as c:
   server.MATLIVE=self.m
   try:
    original_live=c.get('/api/live/state').json();self.assertEqual(c.get('/matlab-live').status_code,200)
    r=c.post('/api/matlab-live/session',json={'client_token':uuid.uuid4().hex});self.assertEqual(r.status_code,202,r.text);id=r.json()['id'];wait(lambda:self.m.snapshot()['phase']=='paused')
    request={'question':'把辐照设为 650 W/m²，关闭云团','source':'auto','page':'/matlab-live','session_id':uuid.uuid4().hex,'client_token':uuid.uuid4().hex}
    reply=c.post('/api/assistant/messages',json=request)
    self.assertEqual(reply.status_code,202,reply.text)
    self.assertEqual(reply.json()['model'],'local_setpoint')
    self.assertEqual(self.m.snapshot()['config']['irradiance'],650)
    self.assertEqual(c.post('/api/assistant/messages',json=request).json()['id'],reply.json()['id'])
    self.assertEqual(c.post('/api/assistant/messages',headers={'Origin':'https://other.example'},json=request).status_code,403)
    self.assertEqual(c.post('/api/matlab/jobs',json={'client_token':uuid.uuid4().hex}).status_code,409)
    self.assertEqual(c.post('/api/matlab-live/config',json=dict(CFG,irradiance=600,session_id=id)).status_code,200);self.assertEqual(c.post('/api/matlab-live/control',json={'action':'step','session_id':id}).status_code,200);wait(lambda:self.m.snapshot()['sample_count']==2000)
    data=c.get('/api/matlab-live/data').json();self.assertEqual(data['rows'][-1]['G_Wm2'],600);self.assertEqual(c.get('/api/live/state').json(),original_live)
    self.assertEqual(c.get('/api/ai/snapshot?source=matlab_live').status_code,400)
    self.assertEqual(c.post('/api/matlab-live/control',json={'action':'start','session_id':'ml_'+'0'*32}).status_code,409)
    self.assertEqual(c.post('/api/matlab-live/control',headers={'Origin':'https://other.example'},json={'action':'start','session_id':id}).status_code,403)
    self.assertEqual(c.post('/api/matlab-live/config',json=dict(CFG,irradiance=1300,session_id=id)).status_code,422)
   finally:server.MATLIVE=original
if __name__=='__main__':
 from unittest import mock
 unittest.main(verbosity=2)
