"""Local, single-worker MATLAB batch jobs. Does not emulate MATLAB results."""
from pathlib import Path
from datetime import datetime,timezone
import os,sys,re,json,uuid,shutil,threading,subprocess,time,signal,zipfile,hashlib
from simulink_bridge import validate,sha,encode
TERMINAL={'completed','captured','failed','cancelled','interrupted'}
SCENARIOS={'test':'cloud_test_input.mat','fast':'cloud_fast_input.mat','slow':'cloud_slow_input.mat','stage':'WG_PV_stage_config.mat'}
ALGORITHMS={'as_saved':'Perturb and observe (P&O)','po':'Perturb and observe (P&O)','inc':'Incremental conductance (INC)'}
def now():return datetime.now(timezone.utc).isoformat()
def read_json(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def discover_matlab():
 configured=os.environ.get('WG_MATLAB_EXE','').strip().strip('"')
 if configured:return str(Path(configured).resolve()) if Path(configured).is_file() else None
 found=shutil.which('matlab')
 if found:return str(Path(found).resolve())
 candidates=[]
 if os.name=='nt':
  base=Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'MATLAB'
  candidates=[base/'R2025a/bin/matlab.exe',*sorted(base.glob('R*/bin/matlab.exe'),reverse=True)]
 elif sys.platform=='darwin':candidates=[Path('/Applications/MATLAB_R2025a.app/bin/matlab'),*sorted(Path('/Applications').glob('MATLAB_R*.app/bin/matlab'),reverse=True)]
 else:candidates=[Path('/usr/local/MATLAB/R2025a/bin/matlab'),*sorted(Path('/usr/local/MATLAB').glob('R*/bin/matlab'),reverse=True)]
 return next((str(p.resolve()) for p in candidates if p.is_file()),None)
def batch_command(executable,workspace,windows=None):
 if windows is None:windows=os.name=='nt'
 return [executable,*(['-wait'] if windows else []),'-sd',str(Path(workspace).resolve()),'-batch','wg_task_entry']
class MatlabJobs:
 def __init__(self,root,adapter,bridge,executable_provider=None,command_factory=None,timeout_s=1200):
  self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.adapter=Path(adapter);self.bridge=bridge
  self.provider=executable_provider or discover_matlab;self.factory=command_factory;self.timeout=timeout_s
  self.lock=threading.RLock();self.wake=threading.Event();self.stopping=threading.Event();self.thread=None;self.active=False;self.problem='';self.jobs={};self.guard=None
 def start(self):
  with self.lock:
   if self.active:return
   self.guard=(self.root/'worker.lock').open('a+b')
   try:
    self.guard.seek(0);self.guard.write(b'0');self.guard.flush();self.guard.seek(0)
    if os.name=='nt':
     import msvcrt
     msvcrt.locking(self.guard.fileno(),msvcrt.LK_NBLCK,1)
    else:
     import fcntl
     fcntl.flock(self.guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except OSError:
    self.guard.close();self.guard=None;self.problem='同一目录已有服务管理MATLAB任务，请关闭重复服务。';return
   self.jobs={}
   for f in sorted(self.root.glob('mat_*/job.json')):
    try:
     item=read_json(f)
     if not re.fullmatch(r'mat_[0-9a-f]{32}',item['id']) or f.parent.name!=item['id']:continue
     self.jobs[item['id']]=item
     if item['status'] not in TERMINAL:self._set(item,'interrupted','服务已重启，旧任务不自动重跑；请确认旧MATLAB进程已结束后再新建任务。')
    except (ValueError,KeyError,OSError):continue
   self.problem='';self.stopping.clear();self.active=True
   self.thread=threading.Thread(target=self._loop,daemon=True,name='matlab-worker');self.thread.start()
 def stop(self):
  self.stopping.set();self.wake.set()
  if self.thread:self.thread.join(timeout=20)
  with self.lock:
   if self.thread and self.thread.is_alive():self.problem='任务清理尚未结束';return
   self.active=False
   if self.guard:self.guard.close();self.guard=None
 def environment(self):
  exe=self.provider();files=['wg_run_for_web.m','wg_task_entry.m','wg_export_native.m','project/WG_PV_Environment.slx','project/ControlThreePhaseSolarInverterParameters.m']
  missing=[f for f in files if not (self.adapter/f).is_file()]
  return {'worker_available':self.active,'executable_found':bool(exe),'executable':exe,'ready':self.active and bool(exe) and not missing,'missing_files':missing,'reason':self.problem or ('未找到MATLAB；安装后设置WG_MATLAB_EXE并重启服务。' if not exe else '已发现执行程序；许可证、工具箱和批处理兼容性在实际启动时检查。'),'timeout_s':self.timeout,'mode':'local_matlab_batch','runs_at_startup':False}
 def _write(self,item):
  p=self.root/item['id']/'job.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(item,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');tmp.replace(p)
 def _set(self,item,status,message):
  item.update(status=status,message=message,updated_at=now());item.setdefault('history',[]).append({'status':status,'at':item['updated_at']})
  if status in TERMINAL:item['finished_at']=item['updated_at']
  self._write(item)
 def create(self,scenario,algorithm,token):
  if scenario not in SCENARIOS or algorithm not in ALGORITHMS or not re.fullmatch(r'[a-f0-9]{32}',token):raise ValueError('工况、算法或请求标识无效')
  with self.lock:
   for item in self.jobs.values():
    if item['client_token']==token:
     if item['scenario']!=scenario or item['algorithm']!=algorithm:raise ValueError('请求标识已用于不同参数')
     return self.detail(item['id'])
   env=self.environment()
   if not env['ready']:raise RuntimeError(env['reason'] if not env['missing_files'] else '适配文件不完整')
   if sum(i['status'] not in TERMINAL for i in self.jobs.values())>=20:raise ValueError('最多20个未结束任务，请等待或取消排队任务')
   id='mat_'+uuid.uuid4().hex;(self.root/id).mkdir()
   item={'id':id,'scenario':scenario,'algorithm':algorithm,'client_token':token,'created_at':now(),'status':'queued','record_id':None}
   self.jobs[id]=item;self._set(item,'queued','已排队；同一服务同时只运行一个MATLAB任务。');self.wake.set();return self.detail(id)
 def _item(self,id):
  if not re.fullmatch(r'mat_[a-f0-9]{32}',id) or id not in self.jobs:raise FileNotFoundError('任务不存在')
  return self.jobs[id]
 def list(self):
  with self.lock:return [json.loads(json.dumps(i)) for i in sorted(self.jobs.values(),key=lambda i:i['created_at'],reverse=True)]
 def detail(self,id):
  with self.lock:
   item=json.loads(json.dumps(self._item(id)));p=self.root/id/'executor.log'
   if p.exists():
    with p.open('rb') as f:f.seek(max(0,p.stat().st_size-8000));item['log_tail']=f.read().decode('utf-8',errors='replace')
   else:item['log_tail']=''
   return item
 def cancel(self,id):
  with self.lock:
   item=self._item(id)
   if item['status']!='queued':raise ValueError('仅可取消尚未运行的排队任务；运行中请等待结束。')
   self._set(item,'cancelled','已取消排队；没有启动MATLAB。');return self.detail(id)
 def _loop(self):
  while not self.stopping.is_set():
   with self.lock:
    item=next((i for i in sorted(self.jobs.values(),key=lambda i:i['created_at']) if i['status']=='queued'),None)
    if item:self._set(item,'starting','准备独立工作副本；尚未确认MATLAB已启动。')
   if item:self._run(item)
   else:self.wake.wait(.5);self.wake.clear()
 def _terminate(self,p):
  if p.poll() is not None:return
  if os.name=='nt':subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],capture_output=True,timeout=10,check=False)
  else:
   try:os.killpg(p.pid,signal.SIGTERM)
   except ProcessLookupError:return
  try:p.wait(timeout=3)
  except subprocess.TimeoutExpired:
   if os.name!='nt':
    try:os.killpg(p.pid,signal.SIGKILL)
    except ProcessLookupError:pass
   else:p.kill()
   p.wait(timeout=3)
 def _run(self,item):
  folder=self.root/item['id'];proc=None
  try:
   workspace=folder/'workspace';shutil.copytree(self.adapter,workspace,ignore=shutil.ignore_patterns('runs','__pycache__'))
   originals=read_json(workspace/'SOURCE_FILES.json')
   for name,m in originals.items():
    if digest(workspace/name)!=m['sha256']:raise ValueError('源模型/参数文件指纹改变：'+name)
   expected={'model_sha256':digest(workspace/'project/WG_PV_Environment.slx'),'parameters_sha256':digest(workspace/'project/ControlThreePhaseSolarInverterParameters.m'),'input_sha256':digest(workspace/'project'/SCENARIOS[item['scenario']])}
   request={k:item[k] for k in ('id','scenario','algorithm')};(folder/'request.json').write_text(json.dumps(request),encoding='utf-8')
   exe=self.provider()
   if not exe:raise RuntimeError('MATLAB执行程序已不可用')
   argv=self.factory(workspace,folder,item) if self.factory else batch_command(exe,workspace)
   env=dict(os.environ,WG_JOB_DIR=str(folder.resolve()));started=time.monotonic()
   with (folder/'executor.log').open('wb') as log:
    proc=subprocess.Popen(argv,cwd=workspace,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,shell=False,start_new_session=os.name!='nt',creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0)
    with self.lock:item.update(pid=proc.pid,started_at=now(),executable=exe);self._set(item,'running','MATLAB进程运行中：可能正在启动、加载模型或求解；无求解百分比。')
    while proc.poll() is None:
     if self.stopping.wait(.2):self._terminate(proc);raise InterruptedError('服务关闭，本任务进程已停止；记录保留，不自动重跑。')
     if time.monotonic()-started>self.timeout:self._terminate(proc);raise TimeoutError('超过任务时间限制，已停止本任务进程。')
    if self.stopping.is_set():raise InterruptedError('服务关闭，任务未进入结果确认。')
    if proc.returncode!=0:raise RuntimeError('MATLAB进程失败，退出码 '+str(proc.returncode)+'；请查看日志与证据包。')
   receipt=read_json(folder/'receipt.json')
   if receipt.get('task_id')!=item['id']:raise ValueError('结果回执任务标识不一致')
   capture=Path(receipt['folder']).resolve()
   if not capture.is_relative_to(workspace.resolve()) or not capture.is_dir():raise ValueError('结果目录不在本任务工作副本中')
   report=read_json(capture/'report.json')
   with self.lock:item['capture_relative']=capture.relative_to(folder.resolve()).as_posix();item['report']=report
   if report.get('web_ready') is not True:
    with self.lock:self._set(item,'captured','采集完成但未满足网站导入条件：'+str(report.get('reason','未说明原因')))
    return
   text=(capture/'web_signals.csv').read_bytes().decode('utf-8');source=read_json(capture/'web_source.json');validate(text,source)
   run=source.get('run',{})
   if source.get('model_name')!='WG_PV_Environment' or run.get('scenario')!=item['scenario'] or run.get('algorithm_run')!=ALGORITHMS[item['algorithm']] or any(run.get(k)!=v for k,v in expected.items()):raise ValueError('结果来源与本任务工况/算法/文件指纹不一致')
   try:record=self.bridge.save('MATLAB · '+item['scenario']+' / '+item['algorithm'],text,source)
   except FileExistsError:record=self.bridge.read('sim_'+sha(text.encode()+encode(source))[:20])['record']
   with self.lock:item['record_id']=record['id'];self._set(item,'completed','本机任务完成，结果经校验已保存；可进入离线结果回放。')
  except Exception as exc:
   if proc and proc.poll() is None:
    try:self._terminate(proc)
    except Exception:pass
   with self.lock:self._set(item,'interrupted' if isinstance(exc,InterruptedError) else 'failed',str(exc))
 def export(self,id):
  with self.lock:
   item=self._item(id)
   if item['status'] not in TERMINAL:raise ValueError('任务尚未结束，请先查看日志')
   folder=self.root/id;dest=folder/'evidence.zip';tmp=folder/'evidence.tmp'
   files=[folder/n for n in ('job.json','request.json','executor.log','receipt.json') if (folder/n).is_file()]
   files += [p for p in (folder/'workspace').rglob('*') if p.is_file() and (p.name in ('failure.json','report.json','web_signals.csv','web_source.json') or p.parent.name=='raw' and p.suffix=='.csv')]
   hashes={}
   with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED) as z:
    for p in files:
     b=p.read_bytes();name=p.relative_to(folder).as_posix();hashes[name]=hashlib.sha256(b).hexdigest();z.writestr(name,b)
    z.writestr('hashes.json',json.dumps(hashes,indent=2))
   tmp.replace(dest);return dest
