"""Relocatable Windows launcher: stdlib first, offline wheels when provided."""
from pathlib import Path
import sys,os,json,hashlib,subprocess,urllib.request,webbrowser,time,contextlib
ROOT=Path(__file__).resolve().parent
CACHE=ROOT/'.runtime';KEY=hashlib.sha256(str(ROOT).encode()).hexdigest()[:24]
PIP_URL='https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl'
PIP_SHA='fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70'

def running_instance(lan=False):
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 for port in range(8766,8786):
  url=f'http://127.0.0.1:{port}'
  try:
   with opener.open(url+'/api/launch/info',timeout=.15) as r:p=json.load(r)
   if p.get('instance_key')==KEY:
    if p.get('lan')!=lan:raise RuntimeError('This folder is already running in a different access mode. Close its server window before switching Local/LAN.')
    return url
  except (OSError,ValueError):continue
 return None

def preflight():
 required=['deepseek_api.py','static/assistant.js','static/assistant.css','matlab_live.py','static/matlab_live.html','static/matlab_live.js','matlab_adapter/wg_live_entry.m','server.py','local_ai.py','portable_ollama.py','setup_deepseek.py','local_access.py','bootstrap.py','requirements-portable.txt','data/catalog.json','data/forecast/year_predictions.csv','static/landing.html','static/entry.js','static/home.html','static/home.js','static/ai.html','static/ai.js','static/premium.css','static/integration/WG_refined_station.glb','software_models/generated/WG_refined_station.blend','matlab_adapter/project/WG_PV_Environment.slx']
 missing=[p for p in required if not (ROOT/p).is_file()]
 if missing:raise RuntimeError('Extract the COMPLETE archive into a writable folder. Missing: '+', '.join(missing))

def prepare_packages():
 lock=ROOT/'portable/requirements.lock';requirements=lock if lock.exists() else ROOT/'requirements-portable.txt';wheels=ROOT/'portable/wheels';manifest=ROOT/'portable/SHA256.json'
 signature=hashlib.sha256(requirements.read_bytes()+ (manifest.read_bytes() if manifest.exists() else b'')).hexdigest();ready=CACHE/'ready.json'
 if ready.exists():
  try:
   if json.loads(ready.read_text())['signature']==signature:
    subprocess.run([sys.executable,'-c','import fastapi,uvicorn,websockets,numpy,scipy'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);return
  except (OSError,ValueError,KeyError,subprocess.SubprocessError):pass
 if manifest.exists():
  for name,digest in json.loads(manifest.read_text()).items():
   p=ROOT/'portable'/name
   if not p.is_relative_to(ROOT/'portable') or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise RuntimeError('Portable package hash failed: '+name)
 pipwheel=wheels/'pip-26.2.2-py3-none-any.whl'
 if not pipwheel.exists():
  print('Downloading official pip bootstrap wheel...',flush=True);wheels.mkdir(parents=True,exist_ok=True)
  with urllib.request.urlopen(PIP_URL,timeout=90) as r:data=r.read()
  if hashlib.sha256(data).hexdigest()!=PIP_SHA:raise RuntimeError('pip SHA256 mismatch')
  pipwheel.write_bytes(data)
 if hashlib.sha256(pipwheel.read_bytes()).hexdigest()!=PIP_SHA:raise RuntimeError('pip wheel integrity check failed')
 try:subprocess.run([sys.executable,'-m','pip','--version'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 except subprocess.CalledProcessError:
  # Use the module entry point; a console-style argv[0]='pip' is rejected on Windows.
  # Ignore wheel metadata on sys.path so pip is installed into this interpreter.
  code="import sys,runpy;w=sys.argv[1];sys.path.insert(0,w);sys.argv=[w,'install','--no-index','--no-deps','--ignore-installed','--no-user','--disable-pip-version-check',w];runpy.run_module('pip',run_name='__main__',alter_sys=True)"
  subprocess.run([sys.executable,'-c',code,str(pipwheel)],check=True)
  subprocess.run([sys.executable,'-m','pip','--version'],check=True)
 args=[sys.executable,'-m','pip','install','--disable-pip-version-check']
 if lock.exists() and wheels.exists():args+=['--no-index','--find-links',str(wheels)]
 else:args+=['--index-url','https://pypi.org/simple','--timeout','90','--retries','2']
 print('Preparing platform packages. Keep this window open...',flush=True);subprocess.run(args+['-r',str(requirements)],check=True)
 subprocess.run([sys.executable,'-c','import fastapi,uvicorn,websockets,numpy,scipy'],check=True)
 ready.write_text(json.dumps({'signature':signature,'python':sys.version,'at':time.time()}),encoding='utf-8')

def desktop_shortcut():
 if os.name!='nt':return
 try:subprocess.run(['cscript.exe','//nologo',str(ROOT/'create_shortcut.vbs')],cwd=ROOT,check=True,timeout=10,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 except (OSError,subprocess.SubprocessError):print('Desktop shortcut could not be created; start_windows.bat remains available.',flush=True)

def open_existing(url):
 if os.environ.get('WG_SETUP_DEEPSEEK')=='1':
  opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
  try:
   with opener.open(url+'/api/ai/status',timeout=8) as response:status=json.load(response)
   if 'deepseek-r1:1.5b' not in status.get('models',[]) and not status.get('active_job'):
    import uuid
    payload=json.dumps({'kind':'prepare','model':'deepseek-r1:1.5b','client_token':uuid.uuid4().hex}).encode()
    req=urllib.request.Request(url+'/api/ai/jobs',data=payload,headers={'Content-Type':'application/json','Origin':url})
    with opener.open(req,timeout=12) as response:json.load(response)
   print('Local AI preparation can be followed on the AI page.',flush=True)
  except Exception as error:print('AI request not confirmed; use Prepare DeepSeek on the AI page: '+str(error),flush=True)
  webbrowser.open(url+'/ai')
 else:webbrowser.open(url)
 print('Opened existing platform: '+url,flush=True)

def setup_deepseek():
 if os.environ.get('WG_SETUP_DEEPSEEK')!='1':return
 result=subprocess.run([sys.executable,'-X','utf8',str(ROOT/'setup_deepseek.py')],cwd=ROOT)
 if result.returncode:print('DeepSeek setup did not complete. The website will still start; retry preparation on its AI page.',flush=True)

def main():
 os.chdir(ROOT);lan=os.environ.get('WG_LAUNCH_LAN')=='1';preflight()
 url=running_instance(lan)
 if url:open_existing(url);return 0
 CACHE.mkdir(exist_ok=True)
 with (CACHE/'setup.lock').open('a+b') as guard:
  guard.write(b'0');guard.flush();guard.seek(0)
  if os.name=='nt':
   import msvcrt
   try:msvcrt.locking(guard.fileno(),msvcrt.LK_NBLCK,1)
   except OSError:raise RuntimeError('This folder is already starting. Please wait for its browser window.')
  url=running_instance(lan)
  if url:open_existing(url);return 0
  prepare_packages();desktop_shortcut();setup_deepseek()
  # Keep setup lock for this server session. Later clicks reuse /api/launch/info first.
  return subprocess.call([sys.executable,'-X','utf8',str(ROOT/'server.py'),*(['--lan'] if lan else [])],cwd=ROOT,env=dict(os.environ,PYTHONUTF8='1'))
if __name__=='__main__':
 try:raise SystemExit(main())
 except Exception:
  import traceback
  text=traceback.format_exc();print(text);CACHE.mkdir(exist_ok=True);(CACHE/'launch_error.log').write_text(text,encoding='utf-8');print('Startup failed. Log: '+str(CACHE/'launch_error.log'));raise SystemExit(1)
