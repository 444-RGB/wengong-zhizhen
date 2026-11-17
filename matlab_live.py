"""Interactive Simulink process: one persistent solver, committed native chunks only."""
from bisect import bisect_right
from pathlib import Path
import csv,hashlib,io,json,math,os,re,shutil,subprocess,threading,time,uuid,zipfile
from matlab_jobs import discover_matlab,now,digest,read_json
FIELDS=['time_s','G_Wm2','Pgrid_W','Qgrid_var','Vdc_V','VdcRef_V']
COMMAND_TRANSPORT='numbered-files-v1'
TERMINAL={'idle','failed','stopped','finished','interrupted'}
def atomic(path,value):
 tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False),encoding='utf-8');tmp.replace(path)
def publish_command(folder,command):
 path=folder/f"command_{command['seq']:08d}.json"
 if path.exists():raise RuntimeError('该序号的仿真指令已发布')
 atomic(path,command)
def config(values):
 allowed={'irradiance','iq_ref_pu','cloud'}
 if set(values)!=allowed:raise ValueError('参数字段不完整或不支持')
 g=values['irradiance'];q=values['iq_ref_pu'];cloud=values['cloud']
 if isinstance(g,bool) or isinstance(q,bool) or not isinstance(g,(int,float)) or not isinstance(q,(int,float)) or not math.isfinite(g) or not math.isfinite(q) or not 0<=g<=1200 or not -.3<=q<=.3 or not isinstance(cloud,bool):raise ValueError('辐照/无功参考参数无效')
 return dict(irradiance=float(g),iq_ref_pu=float(q),cloud=cloud)
class MatlabLive:
 def __init__(self,root,adapter,provider=None,factory=None,timeout=180):
  self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.adapter=Path(adapter);self.provider=provider or discover_matlab;self.factory=factory;self.timeout=timeout
  self.lock=threading.RLock();self.shutdown=threading.Event();self.thread=None;self.proc=None;self.current=None;self.rows=[];self.tokens={}
  self.state={'phase':'idle','id':None,'message':'尚未启动MATLAB交互会话','running_requested':False,'revision':0,'applied_revision':None,'sample_count':0,'latest':None,'backend':'test_fixture' if factory else 'matlab','matlab_execution_confirmed':False}
 def busy(self):
  with self.lock:return self.state['phase'] not in TERMINAL or bool(self.thread and self.thread.is_alive())
 def bridge_ready(self):
  try:
   script=(self.adapter/'wg_live_entry.m').read_text(encoding='utf-8-sig')
  except OSError:
   return False
  return f"bridgeTransport = '{COMMAND_TRANSPORT}';" in script
 def environment(self):return {'executable_found':bool(self.provider()),'mode':'persistent_simulink_stepping','bridge_protocol':COMMAND_TRANSPORT,'bridge_ready':self.bridge_ready(),'required_release':'R2025a','hardware_realtime':False,'max_sim_time_s':30}
 def snapshot(self):
  with self.lock:return json.loads(json.dumps(self.state))
 def activate(self):
  for p in self.root.glob('ml_*/state.json'):
   try:
    s=read_json(p)
    request=read_json(p.parent/'session.json');token=request.get('client_token')
    if isinstance(token,str) and re.fullmatch('[0-9a-f]{32}',token):self.tokens[token]={'id':request['id'],'request':{'algorithm':request['algorithm'],'config':{k:request[k] for k in ('irradiance','iq_ref_pu','cloud')}}}
    if s['phase'] not in TERMINAL:s.update(phase='interrupted',message='服务重启，旧会话不自动续跑');atomic(p,s)
   except (OSError,ValueError,KeyError):pass
 def close(self):
  self.shutdown.set()
  if self.thread:self.thread.join(timeout=7)
  if self.proc and self.proc.poll() is None:self._kill(self.proc)
  if self.thread:self.thread.join(timeout=3)
 def _set(self,**values):
  with self.lock:
   self.state.update(values,updated_at=now())
   if self.current:atomic(self.current/'state.json',self.state)
 def _event(self,action,body):
  if self.current:
   with (self.current/'events.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps({'at':now(),'action':action,'body':body},ensure_ascii=False)+'\n')
 def create(self,algorithm,values,token):
  cfg=config(values)
  if algorithm not in ('po','inc') or not re.fullmatch('[0-9a-f]{32}',token):raise ValueError('算法或请求标识无效')
  key={'algorithm':algorithm,'config':cfg}
  with self.lock:
   if token in self.tokens:
    if self.tokens[token]['request']!=key:raise ValueError('请求标识已被其它参数使用')
    return self.tokens[token]['id']
   if self.busy():raise RuntimeError('已有交互会话；先停止后再新建')
   if not self.bridge_ready():raise RuntimeError('交互仿真后端与 MATLAB 适配器版本不一致，请从同一个完整包启动。')
   exe=self.provider()
   if not exe:raise RuntimeError('未找到本机MATLAB；当前不能启动真实Simulink求解')
   id='ml_'+uuid.uuid4().hex;folder=self.root/id;folder.mkdir();self.current=folder;self.rows=[];self.shutdown.clear()
   self.tokens[token]={'id':id,'request':key};self.state.update(id=id,phase='loading',message='MATLAB启动与模型编译中',running_requested=False,single_step=False,startup_stage=0,applied_config=None,applied_time_s=None,chunk_wall_seconds=None,revision=1,applied_revision=None,sample_count=0,latest=None,config=cfg,algorithm=algorithm,solver_time_s=0.,matlab_execution_confirmed=False)
   atomic(folder/'session.json',{'id':id,'algorithm':algorithm,**cfg,'client_token':token})
   self._set();self._event('create',key)
   self.thread=threading.Thread(target=self._run,args=(exe,folder,id),daemon=True,name='matlab-interactive');self.thread.start();return id
 def configure(self,values,session_id=None):
  cfg=config(values)
  with self.lock:
   if session_id is not None and session_id!=self.state['id']:raise ValueError('会话已切换，请刷新后操作')
   if self.state['phase'] in TERMINAL | {'stopping'}:raise RuntimeError('没有可修改的交互会话')
   if cfg!=self.state['config']:self._set(config=cfg,revision=self.state['revision']+1);self._event('config',{'revision':self.state['revision'],'config':cfg})
   return self.snapshot()
 def command(self,action,session_id=None):
  with self.lock:
   if session_id is not None and session_id!=self.state['id']:raise ValueError('会话已切换，请刷新后操作')
   if action not in ('start','pause','step','stop'):raise ValueError('控制命令无效')
   if not self.busy():raise RuntimeError('没有活动交互会话')
   if action in ('start','step') and self.state['phase'] in ('loading','stopping'):raise RuntimeError('MATLAB尚未就绪')
   if action=='step' and (self.state['running_requested'] or self.state['phase']=='computing' or self.state.get('single_step')):raise RuntimeError('请先暂停并等待当前段完成')
   if action=='stop':self.shutdown.set();self._set(phase='stopping',running_requested=False,message='等待当前计算段结束并保存状态')
   elif action=='pause':self._set(running_requested=False,message='暂停请求已收到，当前段结束后暂停')
   elif action=='start':self._set(running_requested=True,message='连续求解已请求')
   else:self._set(single_step=True)
   self._event(action,{});return self.snapshot()
 def data(self,after=-1,session_id=None):
  if not math.isfinite(after):raise ValueError('采样游标无效')
  with self.lock:
   if session_id is not None and session_id!=self.state['id']:after=-1
   start=bisect_right(self.rows,after,key=lambda row:row['time_s'])
   rows=self.rows[start:start+12000]
   return {'id':self.state['id'],'rows':rows,'more':start+len(rows)<len(self.rows),'state':self.snapshot()}
 def export(self,id):
  if not re.fullmatch(r'ml_[0-9a-f]{32}',id):raise FileNotFoundError('会话不存在')
  folder=self.root/id
  if not folder.is_dir():raise FileNotFoundError('会话不存在')
  out=io.BytesIO()
  with self.lock,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
   hashes={}
   for p in sorted(folder.iterdir()):
    if p.is_file() and (p.suffix in ('.csv','.json','.jsonl','.log')):
     b=p.read_bytes();z.writestr(p.name,b);hashes[p.name]=hashlib.sha256(b).hexdigest()
   z.writestr('SHA256.json',json.dumps(hashes,indent=2))
  return out.getvalue()
 def _kill(self,p):
  if p.poll() is not None:return
  if os.name=='nt':subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],capture_output=True,timeout=10)
  else:
   import signal
   try:os.killpg(p.pid,signal.SIGTERM)
   except ProcessLookupError:return
  try:p.wait(timeout=3)
  except subprocess.TimeoutExpired:p.kill();p.wait(timeout=3)
 def _failure(self,folder):
  path=folder/'failure.json'
  if path.is_file():
   report=read_json(path)
   message=report.get('message') or 'MATLAB 求解失败'
   identifier=report.get('identifier')
   return f'{identifier}：{message}' if identifier else message
 def _exit_error(self,folder,code,stage):
  detail=self._failure(folder)
  if detail:return RuntimeError(detail)
  return RuntimeError(f'MATLAB 在{stage}时退出（退出码 {code}）。请下载会话诊断，查看 matlab.log 与 executor.log。')
 def _wait(self,path,p,deadline):
  startup=path.name=='ready.json'
  stage=0
  while not path.exists():
   if startup:
    for next_stage in range(4,stage,-1):
     marker=path.parent/f'startup_{next_stage}.json'
     if marker.is_file():
      stage=next_stage
      self._set(startup_stage=stage,message=read_json(marker)['message'])
      break
   failure=self._failure(path.parent)
   if failure:raise RuntimeError(failure)
   code=p.poll()
   if code is not None:
    # The child may have published the file just before exiting.
    if path.is_file():break
    raise self._exit_error(path.parent,code,'初始化' if startup else '等待计算结果')
   if self.shutdown.is_set():return None
   if time.monotonic()>deadline:
    detail=self.snapshot()['message'] if startup else '正在求解当前段'
    raise RuntimeError(f'MATLAB 超时（{detail}）。请下载会话诊断查看日志。')
   time.sleep(.05)
  return read_json(path)
 def _accept(self,folder,reply,cmd):
  if reply.get('schema')!='wg-matlab-live-v1' or reply.get('producer')!='wg_live_entry' or reply.get('id')!=cmd['id'] or reply.get('seq')!=cmd['seq'] or reply.get('revision')!=cmd['revision']:raise ValueError('MATLAB响应会话/序号不匹配')
  name=f"chunk_{cmd['seq']:08d}.csv"
  if reply.get('csv')!=name:raise ValueError('采样文件名无效')
  p=folder/name
  if not p.is_file() or p.stat().st_size>3_000_000 or digest(p)!=reply.get('csv_sha256'):raise ValueError('采样文件校验失败')
  reader=csv.DictReader(io.StringIO(p.read_text(encoding='utf-8-sig')))
  if reader.fieldnames!=FIELDS:raise ValueError('采样字段不一致')
  rows=[{k:float(r[k]) for k in FIELDS} for r in reader]
  if not rows or len(rows)>12000 or len(rows)!=reply.get('samples'):raise ValueError('采样数不一致或超出限制')
  last=self.rows[-1]['time_s'] if self.rows else -1
  expected=last+50e-6 if self.rows else 0.
  solver=reply.get('solver_time_s');boundary=reply.get('applied_time_s')
  if not isinstance(solver,(int,float)) or not isinstance(boundary,(int,float)) or not math.isfinite(solver) or not math.isfinite(boundary) or not 0<=boundary<solver<=30.001 or rows[-1]['time_s']>solver or rows[0]['time_s']+1e-9<boundary or abs(boundary-self.state['solver_time_s'])>1e-8:raise ValueError('求解时钟或参数应用边界无效')
  tolerance=1e-4
  previous=self.rows[-1]['G_Wm2'] if self.rows else read_json(folder/'session.json')['irradiance']
  observed=None
  for index,r in enumerate(rows):
   t=r['time_s'];g=r['G_Wm2']
   if not all(math.isfinite(v) for v in r.values()) or not last<t<=30.001 or abs(t-expected)>1e-8:raise ValueError('采样时钟无效')
   if abs(g-cmd['irradiance'])<=tolerance:
    if observed is None:observed=t
   elif index!=0 or abs(t-boundary)>1e-8 or abs(g-previous)>tolerance:
    raise ValueError(f"辐照采样与设定不符：目标 {cmd['irradiance']:g}，实测 {g:g} W/m²（t={t:.6f} s）")
   last=t;expected+=50e-6
  reported=reply.get('irradiance_observed_time_s',boundary)
  if observed is None or not isinstance(reported,(int,float)) or not math.isfinite(reported) or abs(reported-observed)>1e-8:raise ValueError('未获得辐照更新的有效采样证据')
  with self.lock:
   if len(self.rows)+len(rows)>700000:raise ValueError('会话采样超过容量限制')
   self.rows.extend(rows);self._set(phase='stopping' if self.shutdown.is_set() else 'paused',latest=rows[-1],sample_count=len(self.rows),solver_time_s=solver,applied_revision=cmd['revision'],applied_config={k:cmd[k] for k in ('irradiance','iq_ref_pu')},applied_time_s=observed,chunk_wall_seconds=time.monotonic()-self.chunk_started,message='收到新的Simulink计算样本')
 def _run(self,exe,folder,id):
  p=None;seq=0
  try:
   workspace=folder/'workspace';shutil.copytree(self.adapter,workspace,ignore=shutil.ignore_patterns('runs','__pycache__'))
   for n,h in read_json(workspace/'SOURCE_FILES.json').items():
    if digest(workspace/n)!=h['sha256']:raise ValueError('源模型/参数文件指纹改变：'+n)
   request=read_json(folder/'session.json');request['model_sha256']=digest(workspace/'project/WG_PV_Environment.slx');request['command_transport']=COMMAND_TRANSPORT;atomic(folder/'session.json',request)
   cmd=self.factory(exe,workspace,folder) if self.factory else [exe,*(['-wait'] if os.name=='nt' else []),'-logfile',str((folder/'matlab.log').resolve()),'-sd',str(workspace.resolve()),'-batch','wg_live_entry']
   with (folder/'executor.log').open('ab') as log:
    p=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,shell=False,cwd=workspace,env=dict(os.environ,WG_MATLAB_LIVE_DIR=str(folder.resolve())),start_new_session=os.name!='nt');self.proc=p
    ready=self._wait(folder/'ready.json',p,time.monotonic()+max(180,self.timeout))
    if ready is None:return
    if ready.get('command_transport')!=COMMAND_TRANSPORT:raise RuntimeError('MATLAB 适配器版本不匹配，请完整覆盖更新包并重新启动网站。')
    if ready.get('Ts')!=50e-6 or ready.get('id')!=id or ready.get('schema')!='wg-matlab-live-v1' or ready.get('producer')!='wg_live_entry' or ready.get('model_sha256')!=request['model_sha256']:raise ValueError('MATLAB模型初始化凭据无效')
    with self.lock:
     if self.shutdown.is_set():return
     self._set(phase='paused',message='模型已就绪，可开始求解或单步',matlab_execution_confirmed=not bool(self.factory),release=ready.get('release'),single_step=False)
    while not self.shutdown.is_set():
     code=p.poll()
     if code is not None:
      closed=folder/'closed.json'
      if code==0 and closed.is_file():
       record=read_json(closed)
       if record.get('id')==id and record.get('reason')=='idle_timeout':
        self._set(phase='stopped',running_requested=False,message='会话空闲超时，已保留计算结果。请重新加载模型。')
        return
      raise self._exit_error(folder,code,'等待下一段指令')
     with self.lock:
      advance=self.state['running_requested'] or self.state.get('single_step',False)
      cfg=dict(self.state['config']);revision=self.state['revision'];t=self.state['solver_time_s']
      if advance:self._set(phase='computing',single_step=False,message='MATLAB正在计算下一段')
     if not advance:time.sleep(.05);continue
     seq+=1;g=cfg['irradiance']*(1-.6*(.5-.5*math.cos(2*math.pi*t/4))) if cfg['cloud'] else cfg['irradiance']
     command={'id':id,'seq':seq,'action':'advance','revision':revision,'irradiance':g,'iq_ref_pu':cfg['iq_ref_pu'],'delta_s':.1}
     self.chunk_started=time.monotonic();publish_command(folder,command)
     reply=self._wait(folder/f'reply_{seq:08d}.json',p,time.monotonic()+self.timeout)
     if reply is None:break
     self._accept(folder,reply,command)
     if reply.get('final'):
      self._set(phase='finished',running_requested=False,message='到达30模拟秒上限；新建会话可重新实验');break
     time.sleep(max(0,.25-(time.monotonic()-self.chunk_started)))
    seq+=1;publish_command(folder,{'id':id,'seq':seq,'action':'stop'})
    try:code=p.wait(timeout=5)
    except subprocess.TimeoutExpired:self._kill(p)
    else:
     if code!=0:raise self._exit_error(folder,code,'收尾保存结果')
   if self.state['phase']!='finished':self._set(phase='stopped',running_requested=False,message='会话已停止，已提交的数据保留')
  except Exception as error:self._set(phase='failed',running_requested=False,message=str(error)[:1200])
  finally:
   if p and p.poll() is None:self._kill(p)
   self.proc=None
   if self.state['phase']=='stopping' or self.shutdown.is_set() and self.state['phase'] not in TERMINAL:self._set(phase='stopped',running_requested=False,message='会话已停止')
