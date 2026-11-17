"""Paired synthetic transient experiments using the existing reduced-order Model."""
import csv, hashlib, io, json, os, threading, time, uuid, zipfile
from pathlib import Path
from datetime import datetime, timezone
from live_simulation import Model, DEFAULT, FIELDS, MODEL
from experiment_diagnostics import analyse,rule_protocol
DT=.1
SCENARIOS={'cloud_pass':'移动云影','load_step':'限购电下负荷阶跃','export_cap':'出口限额切换'}
def digest(b):return hashlib.sha256(b).hexdigest()
def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,indent=2).encode('utf-8')
def csv_bytes(rows,fields):
 s=io.StringIO(newline='');w=csv.DictWriter(s,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows);return s.getvalue().encode('utf-8')
def protocol(scenario,duration):
 if scenario not in SCENARIOS or duration not in (30,60,120):raise ValueError('请选择有效工况和30、60或120模拟秒')
 inputs=[]
 for step in range(1,duration*10+1):
  phase=min(2,(step-1)//(duration*10//3));c=dict(DEFAULT);c.pop('battery_enabled');c.update(cloud_speed=96/duration)
  if scenario=='cloud_pass':c.update(load_kw=45.,cloud_depth=.85)
  elif scenario=='load_step':c.update(load_kw=90. if phase==1 else 25.,grid_import_kw=0.,cloud_depth=.2)
  else:c.update(irradiance=1000.,cloud_depth=0.,load_kw=15.,grid_export_kw=0. if phase==1 else 60.)
  inputs.append({'step':step,'start_s':round((step-1)*DT,8),'end_s':round(step*DT,8),**c})
 raw=csv_bytes(inputs,list(inputs[0]))
 spec={'schema':'paired-transient-v3-events','diagnostics':rule_protocol(),'scenario':scenario,'title':SCENARIOS[scenario],'duration_s':duration,'dt_s':DT,'steps':len(inputs),'initial_soc':.5,'initial_battery_kwh':30.,'model':MODEL,'inputs_sha256':digest(raw),'phases':[{'start_s':inputs[i]['start_s'],'load_kw':inputs[i]['load_kw'],'grid_export_kw':inputs[i]['grid_export_kw']} for i in (0,len(inputs)//3,2*len(inputs)//3)],'scope':'合成工况、既有简化模型的数值对照；不代表现场验证或硬实时仿真。','integration':'右矩形积分 sum(P[k]*0.1/3600)，初始行dt=0。','terminal_soc':'两组初始SOC相同；末端SOC不约束相等，必须同时查看末端储能差，不用于公平经济性结论。','source_sha256':{n:digest((Path(__file__).parent/n).read_bytes()) for n in ('bench_experiments.py','live_simulation.py','pv_physics.py','experiment_diagnostics.py')}}
 return spec,inputs
SCENE_FIELDS=['seq','time_s','cloud_x']+[f'{field}_{i}' for field in ('irradiance','temperature_C','dc_kw','vmpp_V') for i in range(6)]
def scene_row(state):
 row={k:state[k] for k in ('seq','time_s','cloud_x')}
 for i in range(6):
  row.update({f'irradiance_{i}':sum(state['irradiance_map'][i])/20,f'temperature_C_{i}':state['zone_temperature_C'][i],f'dc_kw_{i}':state['zone_dc_kw'][i],f'vmpp_V_{i}':state['zone_vmpp_V'][i]})
 return row
ENERGY_KEYS=['pv_dc_available_kw','pv_ac_kw','load_kw','charge_kw','discharge_kw','grid_import_kw','grid_export_kw','curtail_kw','unserved_kw']
def metrics(rows):
 m={k[:-2]+'kwh':sum(r[k]*r['dt_s']/3600 for r in rows) for k in ENERGY_KEYS}
 m.update(initial_soc=rows[0]['soc'],final_soc=rows[-1]['soc'],initial_battery_kwh=rows[0]['battery_energy_kwh'],final_battery_kwh=rows[-1]['battery_energy_kwh'],peak_unserved_kw=max(r['unserved_kw'] for r in rows),unserved_duration_s=sum(r['dt_s'] for r in rows if r['unserved_kw']>1e-7),max_ac_residual_kw=max(abs(r['balance_residual_kw']) for r in rows),max_battery_residual_kwh=max(abs(r['battery_residual_kwh']) for r in rows))
 m['battery_transition_residual_kwh']=m['final_battery_kwh']-m['initial_battery_kwh']-.95*m['charge_kwh']+m['discharge_kwh']/.95
 m['ac_energy_residual_kwh']=m['pv_ac_kwh']+m['discharge_kwh']+m['grid_import_kwh']-m['load_kwh']+m['unserved_kwh']-m['charge_kwh']-m['grid_export_kwh']
 return m
class Cancelled(Exception):pass
class Busy(Exception):pass
class Lease:
 def __init__(self,path):self.path=path;self.file=None
 def acquire(self):
  f=self.path.open('a+b');f.seek(0);f.write(b'0');f.flush();f.seek(0)
  try:
   if os.name=='nt':
    import msvcrt;msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
   else:
    import fcntl;fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except OSError:f.close();return False
  self.file=f;return True
 def release(self):
  if self.file:self.file.close();self.file=None
class BenchManager:
 def __init__(self,directory):
  self.root=Path(directory);self.root.mkdir(parents=True,exist_ok=True);self.lock=threading.RLock();self.thread=None;self.cancel_event=threading.Event();self.active=None;self.live_meta=None;self.lease=Lease(self.root/'worker.lock');self.activate()
 def activate(self):
  with self.lock:
   if not self.lease.acquire():return
   try:
    for p in self.root.glob('bench_*/meta.json'):
     m=json.loads(p.read_text(encoding='utf-8'))
     if m['status'] in ('queued','running','cancelling'):m.update(status='interrupted',message='服务中断；保留协议，未生成完整结果。');p.write_bytes(encoded(m))
   finally:self.lease.release()
 def _path(self,id):
  if len(id)!=22 or not id.startswith('bench_') or any(c not in '0123456789abcdef' for c in id[6:]):raise FileNotFoundError(id)
  p=self.root/id
  if not (p/'meta.json').exists():raise FileNotFoundError(id)
  return p
 def list(self):
  items=[]
  for p in self.root.glob('bench_*/meta.json'):
   try:items.append(json.loads(p.read_text(encoding='utf-8')))
   except (OSError,ValueError):continue
  with self.lock:
   if self.live_meta:items=[self.live_meta.copy() if x['id']==self.live_meta['id'] else x for x in items]
  return sorted(items,key=lambda x:x['created_at_utc'],reverse=True)
 def start(self,scenario,duration):
  spec,inputs=protocol(scenario,duration)
  with self.lock:
   if self.thread and self.thread.is_alive():raise Busy('已有实验在计算，请等待或取消。')
   if not self.lease.acquire():raise Busy('另一个服务实例正在计算实验。')
   try:
    id='bench_'+uuid.uuid4().hex[:16];p=self.root/id;p.mkdir();(p/'protocol.json').write_bytes(encoded(spec));(p/'inputs.csv').write_bytes(csv_bytes(inputs,list(inputs[0])))
    self.active=id;self.cancel_event.clear();self.live_meta={'id':id,'scenario':scenario,'title':SCENARIOS[scenario],'duration_s':duration,'created_at_utc':datetime.now(timezone.utc).isoformat(),'status':'queued','completed_steps':0,'total_steps':len(inputs),'elapsed_s':0.};self._save_meta(p)
    self.thread=threading.Thread(target=self._run,args=(p,spec,inputs),daemon=True);self.thread.start();return self.live_meta.copy()
   except Exception:self.lease.release();raise
 def _save_meta(self,p):
  temp=p/'meta.tmp';temp.write_bytes(encoded(self.live_meta));temp.replace(p/'meta.json')
 def _run(self,p,spec,inputs):
  started=time.monotonic()
  try:
   initial={k:v for k,v in inputs[0].items() if k in DEFAULT};models=[Model(dict(initial,battery_enabled=x)) for x in (False,True)];scenes=[scene_row(models[0].last)];traces=[[{'dt_s':0.,**{k:m.last[k] for k in FIELDS}}] for m in models];applied=[[],[]]
   with self.lock:self.live_meta['status']='running';self._save_meta(p)
   for index,row in enumerate(inputs):
    if self.cancel_event.is_set():raise Cancelled()
    c={k:v for k,v in row.items() if k in DEFAULT}
    for j,m in enumerate(models):
     m.config=dict(c,battery_enabled=bool(j));applied[j].append({**{k:row[k] for k in ('step','start_s','end_s')},**{k:m.config[k] for k in c}});r=m.step(DT);traces[j].append({'dt_s':DT,**{k:r[k] for k in FIELDS}})
    scenes.append(scene_row(models[0].last))
    if scene_row(models[0].last)!=scene_row(models[1].last):raise ValueError('分区共同状态不一致')
    if index%10==0:
     with self.lock:
      self.live_meta.update(completed_steps=index+1,elapsed_s=round(time.monotonic()-started,3))
      if index%100==0:self._save_meta(p)
     time.sleep(0)
   if self.cancel_event.is_set():raise Cancelled()
   hashes=[digest(csv_bytes(a,list(inputs[0]))) for a in applied];ms=[metrics(t) for t in traces]
   trajectory_delta=max(abs(a[k]-b[k]) for a,b in zip(*traces) for k in ('pv_dc_available_kw','temperature_C','mean_irradiance'))
   passed=all(h==spec['inputs_sha256'] for h in hashes) and trajectory_delta<1e-10 and all(m['max_ac_residual_kw']<1e-7 and m['max_battery_residual_kwh']<1e-9 and abs(m['battery_transition_residual_kwh'])<1e-9 and abs(m['ac_energy_residual_kwh'])<1e-9 for m in ms) and all(.1-1e-10<=r['soc']<=.9+1e-10 for t in traces for r in t)
   if not passed:raise ValueError('输入或守恒校验未通过，未发布完整结果。')
   result={'metrics':dict(zip(('without_battery','with_battery'),ms)),'delta_with_minus_without':{k:ms[1][k]-ms[0][k] for k in ms[0]},'validation':{'passed':passed,'applied_inputs_sha256':hashes,'max_common_trajectory_delta':trajectory_delta,'soc_bounds_passed':True},'numeric_sha256':digest(encoded(traces))}
   for name,rows in zip(('without_battery','with_battery'),traces):(p/(name+'.csv')).write_bytes(csv_bytes(rows,['dt_s']+FIELDS))
   (p/'scene.csv').write_bytes(csv_bytes(scenes,SCENE_FIELDS))
   result['scene_sha256']=digest((p/'scene.csv').read_bytes())
   source_hashes={n:digest((p/n).read_bytes()) for n in ('inputs.csv','without_battery.csv','with_battery.csv')}
   diagnosis=analyse(p.name,spec,dict(zip(('without_battery','with_battery'),traces)),inputs,source_hashes,result['numeric_sha256'])
   (p/'diagnostics.json').write_bytes(encoded(diagnosis))
   result['diagnostics_sha256']=digest((p/'diagnostics.json').read_bytes())
   (p/'result.json').write_bytes(encoded(result))
   artifacts={n:digest((p/n).read_bytes()) for n in ('protocol.json','inputs.csv','result.json','without_battery.csv','with_battery.csv','scene.csv','diagnostics.json')}
   with self.lock:self.live_meta.update(status='completed',completed_steps=len(inputs),artifact_sha256=artifacts)
  except Cancelled:
   with self.lock:self.live_meta.update(status='cancelled',message='任务已取消；未发布完整结果。')
  except Exception as exc:
   with self.lock:self.live_meta.update(status='failed',message=str(exc))
  finally:
   with self.lock:self.live_meta['elapsed_s']=round(time.monotonic()-started,3);self._save_meta(p);self.active=None;self.lease.release()
 def detail(self,id,series=True):
  with self.lock:
   p=self._path(id);meta=self.live_meta.copy() if self.live_meta and self.live_meta['id']==id else json.loads((p/'meta.json').read_text(encoding='utf-8'));out={'meta':meta,'protocol':json.loads((p/'protocol.json').read_text(encoding='utf-8'))}
   if meta['status']=='completed':
    if any(digest((p/n).read_bytes())!=h for n,h in meta['artifact_sha256'].items()):raise ValueError('文件校验失败，结果可能已被修改。')
    out['result']=json.loads((p/'result.json').read_text(encoding='utf-8'))
    if series:out['series']={n:[{k:float(v) for k,v in r.items()} for r in csv.DictReader(io.StringIO((p/(n+'.csv')).read_text(encoding='utf-8')))] for n in ('without_battery','with_battery')}
   return out
 def scene(self,id):
  out=self.detail(id,True)
  if out['meta']['status']!='completed':raise Busy('实验尚未完成，不能回放完整场景。')
  if 'scene.csv' not in out['meta'].get('artifact_sha256',{}):raise Busy('旧版实验没有分区场景轨迹，请新建实验；原始指标仍可查看。')
  p=self._path(id);frames=[{k:float(v) for k,v in r.items()} for r in csv.DictReader(io.StringIO((p/'scene.csv').read_text(encoding='utf-8')))]
  inputs=[{k:float(v) for k,v in r.items()} for r in csv.DictReader(io.StringIO((p/'inputs.csv').read_text(encoding='utf-8')))]
  series=out['series'];count=out['protocol']['steps']+1
  if len(frames)!=count or any(len(rows)!=count for rows in series.values()):raise ValueError('场景与功率序列长度不一致')
  for index,f in enumerate(frames):
   for rows in series.values():
    if f['seq']!=rows[index]['seq'] or abs(f['time_s']-rows[index]['time_s'])>1e-8:raise ValueError('场景与功率序列时间不一致')
   r=series['without_battery'][index]
   if abs(sum(f[f'dc_kw_{i}'] for i in range(6))-r['pv_dc_available_kw'])>1e-8:raise ValueError('分区DC求和不一致')
   if abs(sum(f[f'temperature_C_{i}'] for i in range(6))/6-r['temperature_C'])>1e-8:raise ValueError('分区温度均值不一致')
   if abs(sum(f[f'irradiance_{i}'] for i in range(6))/6-r['mean_irradiance'])>1e-8:raise ValueError('分区辐照均值不一致')
  return {'id':id,'schema':'recorded-scene-v1','frames':frames,'inputs':inputs,'scene_sha256':out['meta']['artifact_sha256']['scene.csv'],'scope':'读取本次实验保存的分区状态，不重算、不插值；设备与连线为布局示意。'}
 def diagnostics(self,id):
  out=self.detail(id,False)
  if out['meta']['status']!='completed':raise Busy('实验尚未完成，不能读取完整事件诊断。')
  if 'diagnostics.json' not in out['meta'].get('artifact_sha256',{}):raise Busy('旧版实验未保存事件诊断，请新建实验；原始结果仍可查看。')
  report=json.loads((self._path(id)/'diagnostics.json').read_text(encoding='utf-8'))
  if report['id']!=id or report['numeric_sha256']!=out['result']['numeric_sha256'] or any(out['meta']['artifact_sha256'].get(n)!=h for n,h in report['source_sha256'].items()):raise ValueError('诊断来源与实验记录不一致')
  return report
 def cancel(self,id):
  with self.lock:
   if self.active!=id:raise Busy('该任务不是当前可取消的实验。')
   self.cancel_event.set();self.live_meta['status']='cancelling';return self.live_meta.copy()
 def export(self,id):
  out=self.detail(id,False)
  if out['meta']['status']!='completed':raise Busy('仅完整计算且校验通过的结果可导出。')
  p=self._path(id);b=io.BytesIO()
  with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
   names=['meta.json',*out['meta']['artifact_sha256']]
   for n in names:z.writestr(n,(p/n).read_bytes())
   z.writestr('manifest.json',encoded({n:digest((p/n).read_bytes()) for n in names}))
  return b.getvalue()
 def close(self):
  self.cancel_event.set()
  if self.thread:self.thread.join(5)
