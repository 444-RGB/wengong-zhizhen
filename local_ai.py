"""Local Ollama diagnostics; numerical evidence is computed in Python, never by the LLM."""
from pathlib import Path
from datetime import datetime,timezone
import json,hashlib,math,threading,uuid,re,time,os,shutil,subprocess,urllib.request
from pydantic import BaseModel,Field,ConfigDict
import portable_ollama
MODELS=('deepseek-r1:1.5b','deepseek-r1:7b')
TERMINAL={'completed','failed','interrupted'}
BASE='http://127.0.0.1:11434'
def now():return datetime.now(timezone.utc).isoformat()
def encoded(x):return json.dumps(x,ensure_ascii=False,allow_nan=False,sort_keys=True).encode('utf-8')
def sha(x):return hashlib.sha256(encoded(x)).hexdigest()
class Finding(BaseModel):
 model_config=ConfigDict(extra='forbid')
 text:str=Field(min_length=1,max_length=240)
 evidence_ids:list[str]=Field(min_length=1,max_length=5)
class Narrative(BaseModel):
 model_config=ConfigDict(extra='forbid')
 summary:str=Field(min_length=1,max_length=200)
 findings:list[Finding]=Field(max_length=4)
 suggestions:list[str]=Field(max_length=3)
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise RuntimeError('拒绝模型接口重定向')
class Ollama:
 def __init__(self,logdir,base=BASE):self.logdir=Path(logdir);self.base=base
 def _request(self,path,payload=None,timeout=3):
  if path not in ('/api/tags','/api/chat','/api/pull','/api/show'):raise ValueError('接口不允许')
  req=urllib.request.Request(self.base+path,data=encoded(payload) if payload is not None else None,headers={'Content-Type':'application/json'})
  return urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(req,timeout=timeout)
 def json(self,path,payload=None,timeout=3):
  with self._request(path,payload,timeout) as r:
   b=r.read(2_000_001)
   if len(b)>2_000_000:raise ValueError('模型响应过大')
   return json.loads(b)
 def executable(self):
  paths=[os.environ.get('WG_OLLAMA_EXE',''),portable_ollama.installed_executable() or '',shutil.which('ollama') or '']
  if os.name=='nt':paths += [str(Path(os.environ.get('LOCALAPPDATA',''))/'Programs/Ollama/ollama.exe')]
  return next((str(Path(p).resolve()) for p in paths if p and Path(p).is_file()),None)
 def status(self):
  try:
   p=self.json('/api/tags');names=[x['name'] for x in p.get('models',[]) if isinstance(x,dict) and isinstance(x.get('name'),str)]
   return {'connected':True,'installed':True,'models':[m for m in MODELS if m in names],'message':'本地模型服务已连接'}
  except Exception:return {'connected':False,'installed':bool(self.executable()),'models':[],'message':'本地服务未启动' if self.executable() else '尚未准备本地AI运行程序'}
 def ensure(self,stop,progress=None):
  if self.status()['connected']:return
  exe=self.executable()
  if not exe:exe=portable_ollama.prepare_runtime(progress or (lambda p:None),stop)
  self.logdir.mkdir(parents=True,exist_ok=True)
  env=dict(os.environ,OLLAMA_HOST='127.0.0.1:11434',OLLAMA_NO_CLOUD='1')
  if exe==portable_ollama.installed_executable():env['OLLAMA_MODELS']=str(portable_ollama.ROOT/'.runtime/ollama_models')
  with (self.logdir/'ollama.log').open('ab') as log:
   subprocess.Popen([exe,'serve'],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,shell=False,env=env,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
  for _ in range(60):
   if stop.wait(.5):raise RuntimeError('服务关闭，准备已中断')
   if self.status()['connected']:return
  raise RuntimeError('本地AI服务未能及时启动，请查看 runtime/ai/ollama.log 后重试。')
 def pull(self,model,progress,stop):
  self.ensure(stop,progress)
  if model in self.status()['models']:progress({'status':'success'});return
  success=False;started=time.monotonic()
  with self._request('/api/pull',{'model':model,'stream':True},timeout=30) as r:
   last=0
   while True:
    if stop.is_set() or time.monotonic()-started>3600:raise RuntimeError('模型准备已中断或超过一小时；可稍后重试')
    b=r.readline(65537)
    if not b:break
    if len(b)>65536:raise ValueError('模型下载响应格式无效')
    p=json.loads(b)
    if p.get('error'):raise RuntimeError(str(p['error'])[:500])
    success=p.get('status')=='success'
    if time.monotonic()-last>.7 or success:progress(p);last=time.monotonic()
  if not success or model not in self.status()['models']:raise RuntimeError('模型下载未确认完成，请重新检测或重试。')
 def chat(self,model,snapshot):
  prompt='你是光伏仿真分析助手。只用给定的Python数值证据分析，中文简短。不要重新计算数值，不得虚构测量、设备保护限值或故障。因果原因只能写成可能，区分Python实时简化模型与离线Simulink。每个发现必须引用给定metric id。不得执行命令、控制设备或调用工具。按JSON schema返回summary/findings/suggestions；没有足够证据就明确说无法判断。'
  thinking={}
  try:
   controls=self.json('/api/show',{'model':model}).get('thinking',{}).get('values',[])
   if False in controls:thinking={'think':False}
  except Exception:pass
  p=self.json('/api/chat',{'model':model,**thinking,'stream':False,'format':Narrative.model_json_schema(),'keep_alive':'5m','options':{'temperature':0,'num_ctx':8192,'num_predict':4096},'messages':[{'role':'system','content':prompt},{'role':'user','content':encoded(snapshot).decode()}]},timeout=180)
  if p.get('model')!=model or p.get('done') is not True or p.get('done_reason')=='length':raise ValueError('模型输出未完整结束或模型标识不一致')
  text=p.get('message',{}).get('content','');text=re.sub(r'<think>.*?</think>','',text,flags=re.S).strip()
  result=Narrative.model_validate_json(text).model_dump()
  ids={m['id'] for m in snapshot['metrics']}
  if any(k not in ids for f in result['findings'] for k in f['evidence_ids']):raise ValueError('模型引用了不存在的数值证据')
  if any(len(s)>180 for s in result['suggestions']):raise ValueError('建议内容过长')
  return result

def summarize(rows,kind,source_id,run_id,field_map):
 if not rows:raise ValueError('没有可分析采样')
 times=[float(r['time_s']) for r in rows]
 if any(not math.isfinite(t) for t in times) or any(b<=a for a,b in zip(times,times[1:])):raise ValueError('采样时钟无效')
 metrics=[]
 def add(id,label,value,unit):
  if not math.isfinite(float(value)):raise ValueError('非有限数值')
  metrics.append({'id':id,'label':label,'value':value,'unit':unit})
 for field,(name,unit,scale) in field_map.items():
  values=[float(r[field])*scale for r in rows]
  if any(not math.isfinite(v) for v in values):raise ValueError('非有限数值')
  for label,fn in [('min',min),('max',max)]:add(field+'_'+label,name+('最小' if label=='min' else '最大'),fn(values),unit)
  add(field+'_last',name+'末值',values[-1],unit)
  
  if len(rows)>1:add(field+'_change',name+'首末变化',values[-1]-values[0],unit)
  if field in ('pv_ac_kw','Pgrid_W') and len(rows)>1:add('energy_wh','窗口电量',sum((a+b)*.5*(y-x) for a,b,x,y in zip(values,values[1:],times,times[1:]))*1000/3600,'Wh')
 rules=[]
 for field,label,limit in [('unserved_kw','存在供电缺额',1e-6),('balance_residual_kw','功率平衡残差需复核',1e-6)]:
  if field in rows[0] and max(abs(float(r[field])) for r in rows)>limit:rules.append({'text':label,'metric_ids':[field+'_min',field+'_max']})
 if 'soc' in rows[0] and (min(float(r['soc']) for r in rows)<.1-1e-8 or max(float(r['soc']) for r in rows)>.9+1e-8):rules.append({'text':'SOC超出当前简化模型边界','metric_ids':['soc_min','soc_max']})
 snap={'schema':'wg-ai-snapshot-v1','kind':kind,'source_id':source_id,'run_id':run_id,'start_s':times[0],'end_s':times[-1],'samples':len(rows),'metrics':metrics,'rule_findings':rules,'scope':'Numerical evidence from simulation; causal attribution and equipment faults are not established.'}
 snap['snapshot_sha256']=sha(snap);snap['captured_at_utc']=now();return snap

class LocalAI:
 def __init__(self,root,snapshot_provider,client=None):
  self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.provider=snapshot_provider;self.client=client or Ollama(root);self.lock=threading.RLock();self.stop=threading.Event();self.jobs={};self.active=None;self.epoch=0
 def start(self):
  with self.lock:
   self.stop=threading.Event();self.epoch+=1;self.active=None;self.jobs={}
   for p in self.root.glob('ai_*.json'):
    try:
     j=json.loads(p.read_text(encoding='utf-8'))
     if not re.fullmatch(r'ai_[0-9a-f]{32}',j['id']):continue
     if j['status'] not in TERMINAL:j.update(status='interrupted',message='服务重启，任务未自动重跑',finished_at=now());self._save(j)
     self.jobs[j['id']]=j
    except (OSError,ValueError,KeyError):continue
 def close(self):
  with self.lock:
   self.stop.set();self.epoch+=1
   if self.active and self.jobs[self.active]['status'] not in TERMINAL:
    j=self.jobs[self.active];j.update(status='interrupted',message='服务关闭，任务记录保留',finished_at=now());self._save(j)
   self.active=None
 def _save(self,j):
  p=self.root/(j['id']+'.json');t=p.with_suffix('.tmp');t.write_bytes(encoded(j));t.replace(p)
 def status(self):
  s=self.client.status()
  with self.lock:return s|{'active_job':self.active,'available_models':list(MODELS),'endpoint':'local_ollama','cloud_fallback':False}
 def read(self,id):
  with self.lock:
   if not re.fullmatch(r'ai_[0-9a-f]{32}',id) or id not in self.jobs:raise FileNotFoundError('分析记录不存在')
   return json.loads(json.dumps(self.jobs[id]))
 def recent(self):
  with self.lock:return [self.read(j['id']) for j in sorted(self.jobs.values(),key=lambda x:x['created_at'],reverse=True)[:20]]
 def create(self,kind,model,token,source='live',record_id=None):
  if model not in MODELS or kind not in ('prepare','analysis') or not re.fullmatch(r'[0-9a-f]{32}',token):raise ValueError('请求参数无效')
  with self.lock:
   key={'kind':kind,'model':model,'source':source,'record_id':record_id}
   for j in self.jobs.values():
    if j['client_token']==token:
     if j['request']!=key:raise ValueError('请求标识已用于其它参数')
     return self.read(j['id'])
   if self.active:raise RuntimeError('本机已有AI任务，请等待结束')
   snapshot=self.provider(source,record_id) if kind=='analysis' else None
   j={'id':'ai_'+uuid.uuid4().hex,'kind':kind,'model':model,'client_token':token,'request':key,'status':'running','created_at':now(),'message':'准备本地模型' if kind=='prepare' else '分析中','snapshot':snapshot,'result':None}
   self.jobs[j['id']]=j;self.active=j['id'];self._save(j);epoch=self.epoch;stop=self.stop
   threading.Thread(target=self._work,args=(j['id'],epoch,stop),daemon=True,name='local-ai').start();return self.read(j['id'])
 def _update(self,id,epoch,**values):
  with self.lock:
   if self.epoch!=epoch or self.stop.is_set():return False
   self.jobs[id].update(values);self._save(self.jobs[id]);return True
 def _work(self,id,epoch,stop):
  j=self.read(id)
  try:
   if j['kind']=='prepare':
    def progress(p):
     total=p.get('total',0);done=p.get('completed',0);percent=100*done/total if isinstance(total,int) and total>0 and isinstance(done,int) else None
     self._update(id,epoch,message=str(p.get('status','准备中'))[:200],progress={'completed':done,'total':total,'percent':max(0,min(100,percent)) if percent is not None else None})
    self.client.pull(j['model'],progress,stop);self._update(id,epoch,status='completed',message='模型已就绪',finished_at=now())
   else:
    if j['model'] not in self.client.status()['models']:raise RuntimeError('所选模型尚未就绪，请先点击下载并准备DeepSeek。')
    result=self.client.chat(j['model'],j['snapshot']);self._update(id,epoch,status='completed',message='分析完成',result=result,finished_at=now())
  except Exception as e:self._update(id,epoch,status='failed',message=str(e)[:800],finished_at=now())
  finally:
   with self.lock:
    if epoch==self.epoch and self.active==id:self.active=None
