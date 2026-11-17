"""Tests the actual uploaded pair and API, not MATLAB execution."""
import tempfile,os,json,hashlib,io,zipfile,shutil
from pathlib import Path
TMP=tempfile.TemporaryDirectory();os.environ['WG_RUNTIME_DIR']=TMP.name
from fastapi.testclient import TestClient
import server
from paired_capture import PairedCapture
ROOT=Path(__file__).resolve().parent

def run():
 with TestClient(server.app) as c:
  before=c.get('/api/live/state').json();archive=c.get('/api/state').json()
  info=c.get('/api/comparison/info');assert info.status_code==200,info.text;m=info.json()
  assert m['samples']==80001 and m['metadata_differences']==['algorithm_run'] and m['clock_equal']
  assert abs(m['metrics']['po']['energy_Wh']-54.0501991863898)<1e-9
  assert abs(m['metrics']['inc']['energy_Wh']-52.818187897347514)<1e-9
  assert abs(m['metrics']['po']['restore_v_min_V']-629.3133510459321)<1e-9
  assert abs(m['metrics']['inc']['restore_v_max_V']-1016.1872714274986)<1e-9
  compressed=c.get('/api/comparison/data',headers={'Accept-Encoding':'gzip'});assert compressed.headers['content-encoding']=='gzip'
  plain=c.get('/api/comparison/data',headers={'Accept-Encoding':'identity'});assert 'content-encoding' not in plain.headers and plain.content==compressed.content
  assert c.get('/api/comparison/data',headers={'Accept-Encoding':'gzip;q=0'}).content==plain.content
  assert 'content-encoding' not in c.get('/api/comparison/data',headers={'Accept-Encoding':'gzip;q=0.0'}).headers
  d=plain.json();assert len(d['time_s'])==len(d['po'])==len(d['inc'])==80001 and d['time_s'][-1]==4
  assert d['columns']==['G_Wm2','Pgrid_W','Qgrid_var','Vdc_V','VdcRef_V']
  assert all(p[0]==q[0] for p,q in zip(d['po'],d['inc']))
  with zipfile.ZipFile(io.BytesIO(c.get('/api/comparison/export').content)) as z:
   hashes=json.loads(z.read('hashes.json'))
   for n,h in hashes.items():assert hashlib.sha256(z.read(n)).hexdigest()==h and z.read(n)==(ROOT/'data/simulink_comparison'/n).read_bytes()
  assert c.get('/comparison').status_code==200
  assert c.get('/api/live/state').json()==before and c.get('/api/state').json()['dataset']==archive['dataset']
 with tempfile.TemporaryDirectory() as temp:
  root=Path(temp)/'pair';shutil.copytree(ROOT/'data/simulink_comparison',root);p=PairedCapture(root);p.load()
  (root/'po/signals.csv').write_bytes((root/'po/signals.csv').read_bytes()+b'edit')
  try:p.load()
  except ValueError:pass
  else:raise AssertionError('cache failed to detect changed CSV')
 with tempfile.TemporaryDirectory() as temp:
  root=Path(temp)/'pair';shutil.copytree(ROOT/'data/simulink_comparison',root);f=root/'inc/source.json';m=json.loads(f.read_text());m['run']['Ts']=.01;f.write_text(json.dumps(m))
  try:PairedCapture(root).load()
  except ValueError:pass
  else:raise AssertionError('different parameters accepted')
 print('PASS: actual80,001-point source pair,exact metrics,full HTTP arrays,gzip/identity,unchanged ZIP exports,cache tamper detection,parameter mismatch rejection and live isolation. MATLAB not run.')
if __name__=='__main__':run()
