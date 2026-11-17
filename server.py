from pathlib import Path
import csv,json,time,threading,sqlite3,math,asyncio,io,hashlib,html,os
from fastapi import FastAPI,HTTPException,WebSocket,WebSocketDisconnect,Request
from fastapi.responses import FileResponse,HTMLResponse,Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from typing import Literal
ROOT=Path(__file__).resolve().parent;DATA=ROOT/'data';RUNTIME=Path(os.environ.get('WG_RUNTIME_DIR',str(ROOT/'runtime')));RUNTIME.mkdir(exist_ok=True)
DB=RUNTIME/'events.sqlite3';LOCK=threading.RLock();NUM=['time_s','G_Wm2','Pgrid_W','Qgrid_var','Vdc_V','VdcRef_V']
with sqlite3.connect(DB) as c:
 c.execute('CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,created TEXT DEFAULT CURRENT_TIMESTAMP,action TEXT,payload TEXT)')
 c.execute('CREATE TABLE IF NOT EXISTS acknowledgements(id TEXT PRIMARY KEY,created TEXT DEFAULT CURRENT_TIMESTAMP)')
 c.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)')
def record(action,payload):
 with sqlite3.connect(DB) as c:c.execute('INSERT INTO events(action,payload) VALUES(?,?)',(action,json.dumps(payload,ensure_ascii=False)))
def parse_csv(text):
 reader=csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
 if not reader.fieldnames or not set(NUM)<=set(reader.fieldnames):raise ValueError('CSV需要六列：'+', '.join(NUM))
 rows=[]
 for line,r in enumerate(reader,2):
  try:v={k:float(r[k]) for k in NUM}
  except (TypeError,ValueError):raise ValueError(f'第{line}行数值无效')
  if not all(math.isfinite(x) for x in v.values()) or v['time_s']<0 or v['G_Wm2']<0:raise ValueError(f'第{line}行存在缺失、非有限值或负时间/辐照')
  if rows and v['time_s']<=rows[-1]['time_s']:raise ValueError(f'第{line}行时间未严格递增')
  rows.append(v)
  if len(rows)>20000:raise ValueError('本地原型每个导入文件最多20000条记录')
 if len(rows)<2:raise ValueError('至少需要两条记录')
 if rows[0]['time_s']!=0:raise ValueError('回放时间必须从0秒开始')
 return rows
CATALOG=json.loads((DATA/'catalog.json').read_text(encoding='utf-8-sig'))
if (RUNTIME/'imports.json').exists():CATALOG+=json.loads((RUNTIME/'imports.json').read_text(encoding='utf-8-sig'))
SETS={}
for e in CATALOG:
 path=(RUNTIME if e.get('imported') else DATA)/e['file'];SETS[e['id']]={'entry':e,'path':path,'rows':parse_csv(path.read_text(encoding='utf-8-sig'))}
STATE={'dataset':'po','cursor':0.,'speed':.25,'playing':False,'anchor':time.monotonic()}
DEFAULT_RULES={'min_voltage':750.,'max_voltage':950.,'ramp_kw_s':20.,'min_duration':.03,'ignore_before':.3}
with sqlite3.connect(DB) as c:r=c.execute("SELECT value FROM settings WHERE key='rules'").fetchone()
RULES=json.loads(r[0]) if r else DEFAULT_RULES.copy();ALERT_CACHE={}
def current():return SETS[STATE['dataset']]
def clock():
 if STATE['playing']:
  now=time.monotonic();STATE['cursor']=min(current()['rows'][-1]['time_s'],STATE['cursor']+(now-STATE['anchor'])*STATE['speed']);STATE['anchor']=now
  if STATE['cursor']>=current()['rows'][-1]['time_s']:STATE['playing']=False
 return STATE['cursor']
def snapshot():
 import bisect
 with LOCK:
  t=clock();ds=current();rows=ds['rows'];i=max(0,bisect.bisect_right([r['time_s'] for r in rows],t)-1)
  return {'dataset':STATE['dataset'],'title':ds['entry']['title'],'playing':STATE['playing'],'speed':STATE['speed'],'cursor':t,'duration':rows[-1]['time_s'],'index':i,'sample':rows[i],'forecast_status':'independent_historical_replay','storage_status':'independent_synthetic_planning_optional_dependency'}
def integrate(rows):return sum((a['Pgrid_W']+b['Pgrid_W'])*.5*(b['time_s']-a['time_s']) for a,b in zip(rows,rows[1:]))/3600

def summary(ds):
 rows=ds['rows'];e=ds['entry'];duration=rows[-1]['time_s'];out={'id':e['id'],'title':e['title'],'samples':len(rows),'duration_s':duration,'total_energy_Wh':integrate(rows),'source':e['source'],'imported':bool(e.get('imported')),'input_hash':hashlib.sha256(json.dumps([[r['time_s'],r['G_Wm2']] for r in rows]).encode()).hexdigest()}
 win=e.get('recovery_window')
 if win:
  x=[r for r in rows if win[0]<=r['time_s']<=win[1]]
  out.update(window_s=win,mean_power_kw=integrate(x)*3.6/(win[1]-win[0]),minimum_kw=min(r['Pgrid_W'] for r in x)/1000,recovery_energy_Wh=integrate(x),vmin=min(r['Vdc_V'] for r in x),vmax=max(r['Vdc_V'] for r in x))
 return out

def diagnostic(ds):
 revision=hashlib.sha256(json.dumps(RULES,sort_keys=True).encode()).hexdigest()[:10];key=ds['entry']['id']+revision
 if key not in ALERT_CACHE:
  rows=ds['rows'];found=[];conditions=[]
  # Measured ramp over >=50 ms. This is a retrospective indicator, not a forecast.
  j=0
  for i,r in enumerate(rows):
   while j+1<i and rows[j+1]['time_s']<=r['time_s']-.05:j+=1
   dt=r['time_s']-rows[j]['time_s'];ramp=(r['Pgrid_W']-rows[j]['Pgrid_W'])/1000/dt if dt>=.05 else 0.
   conditions.append((r['time_s']>=RULES['ignore_before'],r['Vdc_V']>RULES['max_voltage'],r['Vdc_V']<RULES['min_voltage'],abs(ramp)>RULES['ramp_kw_s'],ramp))
  for code,title,k in [('high_voltage','直流电压高于配置阈值',1),('low_voltage','直流电压低于配置阈值',2),('ramp','并网功率快速变化',3)]:
   begin=None
   for i,c in enumerate(conditions+[(False,False,False,False,0)]):
    active=c[0] and c[k]
    if active and begin is None:begin=i
    if not active and begin is not None:
     end=i-1;start_t=rows[begin]['time_s'];end_t=rows[end]['time_s']
     if end_t-start_t>=RULES['min_duration']:
      vals=[abs(conditions[n][4]) if code=='ramp' else rows[n]['Vdc_V'] for n in range(begin,end+1)]
      found.append({'id':f"{key}:{code}:{begin}",'code':code,'title':title,'start_s':start_t,'end_s':end_t,'duration_s':end_t-start_t,'extreme':min(vals) if code=='low_voltage' else max(vals),'unit':'kW/s' if code=='ramp' else 'V','threshold':RULES['ramp_kw_s'] if code=='ramp' else RULES['max_voltage'] if code=='high_voltage' else RULES['min_voltage'],'classification':'已发生事件 / 非提前预警'})
     begin=None
  ALERT_CACHE[key]=sorted(found,key=lambda x:x['start_s'])
 with sqlite3.connect(DB) as c:acks={r[0] for r in c.execute('SELECT id FROM acknowledgements')}
 return [dict(r,acknowledged=r['id'] in acks) for r in ALERT_CACHE[key]]
from contextlib import asynccontextmanager
@asynccontextmanager
async def lifespan(app):
 start_live_engine()
 try:yield
 finally:stop_live_engine()
app=FastAPI(title='稳供智诊 · 仿真运行工作台',version='2.7.0',lifespan=lifespan)
from local_access import LocalAccess,is_local
ACCESS={'lan':False,'token':'','port':8766}
INSTANCE_KEY=hashlib.sha256(str(ROOT).encode()).hexdigest()[:24]
app.add_middleware(LocalAccess,settings=lambda:ACCESS)
class Control(BaseModel):action:str;value:float|None=None
class Select(BaseModel):dataset:str
class RuleInput(BaseModel):
 min_voltage:float=Field(ge=0,lt=2000)
 max_voltage:float=Field(gt=0,le=3000)
 ramp_kw_s:float=Field(gt=0,le=10000)
 min_duration:float=Field(ge=.001,le=10)
 ignore_before:float=Field(ge=0,le=30)
class ImportInput(BaseModel):
 title:str=Field(min_length=1,max_length=60)
 csv_text:str=Field(max_length=5_000_000)
@app.get('/api/health')
def health():return {'status':'ok','mode':'offline_simulation_replay','samples':len(current()['rows'])}
@app.get('/api/state')
def state():return snapshot()
@app.get('/api/series')
def series(dataset:str|None=None):
 if dataset is not None and dataset not in SETS:raise HTTPException(404,'数据集不存在')
 return (SETS[dataset] if dataset else current())['rows']
@app.get('/api/datasets')
def datasets():return [summary(ds) for ds in SETS.values()]
@app.post('/api/select')
def select(body:Select):
 if body.dataset not in SETS:raise HTTPException(404,'数据集不存在')
 with LOCK:
  STATE.update(dataset=body.dataset,cursor=0.,playing=False,anchor=time.monotonic());record('select',{'dataset':body.dataset});return snapshot()
@app.get('/api/source')
def source():
 ds=current();return {'dataset':ds['entry'],'units':{'time_s':'s','G_Wm2':'W/m²','Pgrid_W':'W','Qgrid_var':'var','Vdc_V':'V','VdcRef_V':'V'},'forecast':'未接入','storage':'未接入','geometry':'等值布局示意，非现场测绘或分区阴影电气模型','diagnosis':'回放测量诊断，不是天气预测或设备保护定值'}
@app.post('/api/control')
def control(c:Control):
 if c.action not in ['play','pause','seek','speed','reset']:raise HTTPException(400,'未知操作')
 if c.action in ['seek','speed'] and (c.value is None or not math.isfinite(c.value)):raise HTTPException(400,'缺少有效数值')
 if c.action=='speed' and c.value not in [.1,.25,.5,1,2]:raise HTTPException(400,'不支持的速度')
 if c.action=='seek' and not 0<=c.value<=current()['rows'][-1]['time_s']:raise HTTPException(400,'时间超出范围')
 with LOCK:
  clock()
  if c.action=='play':
   if STATE['cursor']>=current()['rows'][-1]['time_s']:STATE['cursor']=0
   STATE['playing']=True
  elif c.action=='pause':STATE['playing']=False
  elif c.action=='seek':STATE['cursor']=c.value
  elif c.action=='reset':STATE.update(cursor=0.,playing=False)
  elif c.action=='speed':STATE['speed']=c.value
  STATE['anchor']=time.monotonic();record(c.action,{'dataset':STATE['dataset'],'cursor':STATE['cursor'],'speed':STATE['speed']});return snapshot()
@app.get('/api/export')
def export():return FileResponse(current()['path'],media_type='text/csv',filename=current()['path'].name)
@app.get('/api/events')
def events():
 with sqlite3.connect(DB) as c:return [{'id':r[0],'created':r[1],'action':r[2],'payload':json.loads(r[3])} for r in c.execute('SELECT id,created,action,payload FROM events ORDER BY id DESC LIMIT 100')]
@app.get('/api/rules')
def rules():return RULES
@app.put('/api/rules')
def set_rules(body:RuleInput):
 global RULES
 v=body.model_dump()
 if v['min_voltage']>=v['max_voltage']:raise HTTPException(400,'电压下限必须小于上限')
 with LOCK:
  RULES=v;ALERT_CACHE.clear()
  with sqlite3.connect(DB) as c:c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('rules',?)",(json.dumps(v),))
  record('rules_update',v)
 return RULES
@app.get('/api/alerts')
def alerts():return diagnostic(current())
@app.post('/api/alerts/{alert_id}/ack')
def acknowledge(alert_id:str):
 if alert_id not in {r['id'] for r in diagnostic(current())}:raise HTTPException(404,'事件不存在或规则已更新')
 with sqlite3.connect(DB) as c:c.execute('INSERT OR IGNORE INTO acknowledgements(id) VALUES(?)',(alert_id,))
 record('acknowledge',{'id':alert_id});return {'status':'acknowledged'}
@app.post('/api/import')
def import_csv(body:ImportInput):
 try:rows=parse_csv(body.csv_text)
 except ValueError as e:raise HTTPException(400,str(e))
 id='import_'+hashlib.sha256(body.csv_text.encode()).hexdigest()[:12]
 with LOCK:
  if id in SETS:raise HTTPException(409,'该文件已经导入')
  path=RUNTIME/(id+'.csv');path.write_text(body.csv_text,encoding='utf-8')
  entry={'id':id,'title':body.title,'file':path.name,'source':'用户导入 / 来源未独立核验','mode':'offline_replay','imported':True,'metadata':{}}
  SETS[id]={'entry':entry,'path':path,'rows':rows};CATALOG.append(entry)
  (RUNTIME/'imports.json').write_text(json.dumps([e for e in CATALOG if e.get('imported')],ensure_ascii=False),encoding='utf-8');record('import',{'dataset':id,'rows':len(rows)})
 return summary(SETS[id])
@app.get('/api/report')
def report():
 ds=current();s=summary(ds);a=diagnostic(ds);title=html.escape(s['title']);table=''.join(f"<tr><td>{html.escape(r['title'])}</td><td>{r['start_s']:.3f}—{r['end_s']:.3f} s</td><td>{r['extreme']:.2f} {r['unit']}</td><td>{'已确认' if r['acknowledged'] else '待复核'}</td></tr>" for r in a)
 comparison=''.join(f"<tr><td>{html.escape(x['title'])}</td><td>{x.get('mean_power_kw',0):.3f}</td><td>{x.get('recovery_energy_Wh',0):.3f}</td></tr>" for x in datasets() if 'window_s' in x)
 page=f'''<!doctype html><meta charset="utf-8"><title>稳供智诊运行复盘</title><style>body{{font:14px/1.9 sans-serif;max-width:1000px;margin:45px auto;color:#294138}}h1{{border-bottom:3px solid #b6a472;padding-bottom:15px}}table{{border-collapse:collapse;width:100%;margin:20px 0}}td,th{{border:1px solid #d9dfd1;padding:10px;text-align:left}}small{{color:#798472}}pre{{white-space:pre-wrap}}@media print{{button{{display:none}}}}</style><button onclick="print()">打印 / 保存PDF</button><h1>稳供智诊 · 仿真运行复盘</h1><h2>{title}</h2><p>数据：{s['samples']}条，{s['duration_s']:.3f}模拟秒；积分并网电量：{s['total_energy_Wh']:.4f} Wh。</p><p>本报告由离线仿真输出生成。几何场景仅示意，小时级预测另设历史回放页，与本6秒仿真无时钟或单位映射。储能规划另设合成沙箱，与本6秒仿真未联机；未接入真实电站或设备控制。</p><h2>测量事件</h2><p>规则为本地可调诊断阈值，不是保护定值或提前预警。启动忽略段与最短持续时间见配置。</p><table><tr><th>事件</th><th>区间</th><th>极值</th><th>复核状态</th></tr>{table}</table><h2>同输入配置对照</h2><p>以下三个内置工况采用相同辐照与回放时间轴，窗口2.5—4.5秒。指标由1ms回放数据计算，不代表MPPT效率或全年收益。</p><table><tr><th>配置</th><th>窗口平均功率/kW</th><th>窗口并网电量/Wh</th></tr>{comparison}</table><h2>参数与来源</h2><pre>{html.escape(json.dumps(source(),ensure_ascii=False,indent=2))}</pre><h2>诊断规则</h2><pre>{html.escape(json.dumps(RULES,ensure_ascii=False,indent=2))}</pre>'''
 return HTMLResponse(page,headers={'Content-Disposition':'attachment; filename="wg_review.html"'})
@app.websocket('/ws/telemetry')
async def telemetry(ws:WebSocket):
 await ws.accept()
 try:
  while True:await ws.send_json(snapshot());await asyncio.sleep(.1)
 except (WebSocketDisconnect,RuntimeError):pass
# Scientific forecast replay uses a separate source clock/unit system.
from datetime import datetime,timedelta
FORECAST_DIR=DATA/'forecast'
with (FORECAST_DIR/'year_predictions.csv').open(encoding='utf-8-sig') as f:FORECAST=list(csv.DictReader(f))
with (FORECAST_DIR/'year_metrics.csv').open(encoding='utf-8-sig') as f:YEAR_METRICS=list(csv.DictReader(f))
MODEL_KEYS=['fusion_V27_primary','Persistence','tree_expanding_monthly','Cross_raw','GRU_raw']
REPORT_DENOMINATOR=63.2248784507
@app.get('/api/forecast/info')
def forecast_info():
 return {'n':len(FORECAST),'first_origin':FORECAST[0]['origin_label'],'last_origin':FORECAST[-1]['origin_label'],'last_target':FORECAST[-1]['target_label'],'unit':'源功率单位（未独立认证为kW）','clock':'原始源时钟（未认证UTC）','site':'单一KDASC聚合站点，非青海沙戈荒现场验证','mode':'已封存结果的逐时历史回放，不进行实时模型推理','weights':[.277869078691768,.3061581814613552,.41597273984687677],'status_mask':'历史低发电轮廓，不等于总体气象状态或经认证的夜间','limitations':['2018已开封，不再用于调参或重新称盲测','功能复核重建版，非原Cross完整状态逐字节恢复','无预测区间、无动态气象门控、无储能收益证明','不将源功率点值直接换算为kWh或映射到60kW虚拟场站'],'normalization':'固定初始前缀功率标准差，不是装机容量','report_denominator':REPORT_DENOMINATOR}
@app.get('/api/forecast/frame')
def forecast_frame(index:int=0):
 if not 0<=index<len(FORECAST):raise HTTPException(400,'起报索引超出范围')
 row=FORECAST[index];origin=row['origin_label'];day=origin[:10]
 # No current-target truth is sent. Past truth is visible only once target<=origin.
 trace=[]
 for r in FORECAST[max(0,index-30):index+1]:
  if r['target_label'][:10]!=day and r is not row:continue
  trace.append({'target_label':r['target_label'],'forecast':float(r['fusion_V27_primary']),'persistence':float(r['Persistence']),'actual':float(r['target']) if r['target_label']<=origin else None})
 return {'index':index,'origin_label':origin,'target_label':row['target_label'],'expected_low':row['expected_low']=='True','forecasts':{k:float(row[k]) for k in MODEL_KEYS},'actual_target':None,'truth_status':'目标尚未到达，当前目标实测不提供给决策回放','trace':trace}
@app.get('/api/forecast/metrics')
def forecast_metrics(month:str|None=None):
 if month is not None:
  if len(month)!=7 or month[:5]!='2018-' or month[5:] not in [f'{i:02}' for i in range(1,13)]:raise HTTPException(400,'月份格式需为2018-MM')
 selected=[r for r in FORECAST if month is None or r['origin_label'].startswith(month)]
 result=[]
 for key in MODEL_KEYS:
  errors=[float(r[key])-float(r['target']) for r in selected];rmse=math.sqrt(math.fsum(e*e for e in errors)/len(errors));mae=math.fsum(abs(e) for e in errors)/len(errors)
  result.append({'model':key,'n':len(errors),'RMSE_source_units':rmse,'MAE_source_units':mae,'nRMSE':rmse/REPORT_DENOMINATOR})
 return {'mode':'事后评分，包含目标实测，仅用于独立复盘','scope':month or '2018全年起报','models':result,'significance':'相对逐月LightGBM的7日块差值区间跨零，不能宣称稳定超越强单模型','source_state':'2018已开封 / 功能复核重建版 / 单一聚合站点'}
@app.get('/api/forecast/export')
def forecast_export():
 # Only origin+target+prediction; deliberately exclude truth and other future labels.
 buf=io.StringIO();w=csv.writer(buf);w.writerow(['origin_label','target_label','expected_low','forecast_1h_point_source_units'])
 for r in FORECAST:w.writerow([r['origin_label'],r['target_label'],r['expected_low'],r['fusion_V27_primary']])
 return Response('\ufeff'+buf.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="forecast_points_source_units.csv"'})


# Independent, quasi-static DC model; no mapping to archived AC replay or forecast source units.
from pv_physics import solve as dc_solve,search_mppt,REFERENCE
from pydantic import ConfigDict
from functools import lru_cache
class PhysicsInput(BaseModel):
 model_config=ConfigDict(allow_inf_nan=False)
 irradiance:float=Field(1000,ge=0,le=1300)
 temperature:float=Field(25,ge=-20,le=85)
 transmission:float=Field(1,ge=.3,le=1)
 shade:list[list[float]]=Field(default_factory=lambda:[[1.]*20 for _ in range(6)])
 def checked(self):
  if len(self.shade)!=6 or any(len(s)!=20 for s in self.shade) or any(not math.isfinite(x) or not 0<=x<=1 for row in self.shade for x in row):raise HTTPException(400,'遮挡矩阵须为6组×20组件，透射率在0—1之间')
  return self.model_dump()
class SearchInput(PhysicsInput):
 start_voltage:float=Field(800,ge=0,le=2000)
 step:float=Field(5,ge=.1,le=50)
@lru_cache(maxsize=24)
def cached_dc(config_json):return dc_solve(json.loads(config_json))
def dc_result(body):return cached_dc(json.dumps(body.checked(),sort_keys=True))
@app.get('/api/physics/info')
def physics_info():return {'model':'Single-diode / De Soto temperature-irradiance scaling / ideal constant-drop bypass','reference':REFERENCE,'scope':'合成参数，6串并联、每串20组件、一个公共DC工作电压；组件内均匀受光；非现有Simulink60kW模型参数辨识','not_included':['电池级局部遮挡与雪崩击穿','热斑、温度动态和逆变器损耗','组件老化与真实积尘机理','真实场站参数标定、气象预测与储能闭环'],'references':['https://pvlib-python.readthedocs.io/en/stable/reference/generated/pvlib.pvsystem.calcparams_desoto.html','https://pvlib-python.readthedocs.io/en/stable/user_guide/modeling_topics/singlediode.html'],'resolution':{'current_samples_per_string':1601,'array_voltage_samples':1201}}
@app.post('/api/physics/solve')
def physics_solve(body:PhysicsInput):
 result=dc_result(body);record('dc_model_solve',{'config_sha256':result['config_sha256'],'pmp_W':result['pmp_W'],'config':body.checked()});return result
@app.post('/api/physics/search')
def physics_search(body:SearchInput):
 config=PhysicsInput(irradiance=body.irradiance,temperature=body.temperature,transmission=body.transmission,shade=body.shade)
 return search_mppt(dc_result(config),body.start_voltage,body.step)
@app.post('/api/physics/scan')
def physics_scan(body:PhysicsInput):
 config=body.checked();points=[]
 for axis,values in [('irradiance',[200,400,600,800,1000,1200]),('temperature',[-10,0,15,25,40,55,70,85])]:
  for value in values:
   c={**config,axis:float(value)};r=cached_dc(json.dumps(c,sort_keys=True));points.append({'axis':axis,'value':value,'pmp_W':r['pmp_W'],'vmp_V':r['vmp_V']})
 return {'mode':'准静态参数扫描，其他输入保持不变','points':points}
@app.post('/api/physics/export')
def physics_export(body:PhysicsInput):
 result=dc_result(body);buf=io.StringIO();w=csv.DictWriter(buf,fieldnames=['voltage_V','current_A','power_W']);w.writeheader();w.writerows(result['curve'])
 return Response('\ufeff'+buf.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="pv_lab_iv.csv"'})


from spatial_model import scan as ray_scan,shading as geometric_shadow,pcd_text
class SpatialInput(BaseModel):
 model_config=ConfigDict(allow_inf_nan=False)
 obstacle_height:float=Field(0,ge=0,le=12)
 stations:int=Field(4,ge=1,le=4)
 azimuth_samples:int=Field(720,ge=180,le=720)
 vertical_samples:int=Field(64,ge=16,le=64)
 max_range:float=Field(100,ge=30,le=180)
 range_noise:float=Field(.02,ge=0,le=.2)
 dropout:float=Field(.05,ge=0,le=.6)
 seed:int=Field(42,ge=0,le=1000000000)
class ShadowInput(BaseModel):
 model_config=ConfigDict(allow_inf_nan=False)
 sun_azimuth:float=Field(0,ge=0,le=360)
 sun_elevation:float=Field(15,ge=1,le=89)
 dni:float=Field(800,ge=0,le=1100)
 diffuse_poa:float=Field(100,ge=0,le=200)
 temperature:float=Field(25,ge=-20,le=85)
 obstacle_height:float=Field(0,ge=0,le=12)
 samples:int=Field(3,ge=3,le=9)
@lru_cache(maxsize=4)
def cached_scan(c):return ray_scan(json.loads(c))
@app.post('/api/spatial/scan')
def spatial_scan(body:SpatialInput):
 result=cached_scan(json.dumps(body.model_dump(),sort_keys=True));record('virtual_ray_scan',{'config':body.model_dump(),'points':result['point_count']});return result
@app.post('/api/spatial/export')
def spatial_export(body:SpatialInput):
 result=cached_scan(json.dumps(body.model_dump(),sort_keys=True));return Response(pcd_text(result),media_type='text/plain',headers={'Content-Disposition':'attachment; filename="wg_synthetic_scan.pcd"'})
@app.post('/api/spatial/shadow')
def spatial_shadow(body:ShadowInput):
 config=body.model_dump();s=geometric_shadow(config);dc=dc_result(PhysicsInput(**s['physics_config']));clear_config={**s['physics_config'],'shade':[[1.]*20 for _ in range(6)]};clear=dc_result(PhysicsInput(**clear_config))
 s['dc_summary']={k:dc[k] for k in ['pmp_W','vmp_V','mismatch_loss_W','local_peaks']};s['reference_unoccluded_W']=clear['pmp_W'];s['occlusion_power_difference_W']=clear['pmp_W']-dc['pmp_W'];record('geometry_to_dc',{'geometry_config':config,'dc_config_sha256':dc['config_sha256'],'pmp_W':dc['pmp_W']});return s


# Time-interval target results remain separate from endpoint research and synthetic kWh fixtures.
ENERGY_DIR=DATA/'energy'
ENERGY={}
for seed in [20261026,20261027,20261028]:
 with (ENERGY_DIR/f'{seed}_predictions.csv').open(encoding='utf-8-sig') as f:ENERGY[seed]=list(csv.DictReader(f))
with (ENERGY_DIR/'summary.csv').open(encoding='utf-8-sig') as f:ENERGY_SUMMARY=list(csv.DictReader(f))
with (ENERGY_DIR/'fusion_weights.csv').open(encoding='utf-8-sig') as f:ENERGY_WEIGHTS=list(csv.DictReader(f))
ENERGY_MODELS=['fusion_energy','direct_energy_tree','Persistence_energy','fusion_equal','V27_linear_bridge']
def energy_rows(seed):
 if seed not in ENERGY:raise HTTPException(400,'种子必须为20261026/20261027/20261028')
 return ENERGY[seed]
@app.get('/api/energy/info')
def energy_info():return {'metadata':json.loads((ENERGY_DIR/'forecast_interface_metadata.json').read_text(encoding='utf-8')),'summary':[{k:v if k=='model' else float(v) for k,v in r.items()} for r in ENERGY_SUMMARY],'weights':ENERGY_WEIGHTS,'n_per_seed':len(ENERGY[20261026]),'first_origin':ENERGY[20261026][0]['origin_label'],'last_origin':ENERGY[20261026][-1]['origin_label'],'mode':'已开封2017开发段；仅回放归档结果，非在线推理','target':'未来一小时的复合梯形积分近似，不是置信区间','unit':'源功率单位×h，未认证kWh','primary_seed':20261026,'conclusion':'融合平均RMSE尚不如直接区间目标树，MAE较低；不是最优性证明'}
@app.get('/api/energy/frame')
def energy_frame(seed:int=20261026,index:int=0):
 rows=energy_rows(seed)
 if not 0<=index<len(rows):raise HTTPException(400,'索引超出范围')
 row=rows[index];origin=row['origin_label'];trace=[]
 for r in rows[max(0,index-30):index+1]:
  if r['interval_end_label'][:10]!=origin[:10] and r is not row:continue
  trace.append({'target_label':r['interval_end_label'],'forecast':float(r['fusion_energy']),'tree':float(r['direct_energy_tree']),'actual':float(r['target_integral_approx_source_power_hour']) if r['interval_end_label']<=origin else None})
 return {'seed':seed,'index':index,'origin_label':origin,'interval_end_label':row['interval_end_label'],'expected_low':row['expected_low']=='True','forecasts':{k:float(row[k]) for k in ENERGY_MODELS},'actual_target':None,'trace':trace,'unit':'源功率单位×h'}
@app.get('/api/energy/metrics')
def energy_metrics(seed:int=20261026):
 rows=energy_rows(seed);result=[]
 for model in ENERGY_MODELS:
  errors=[float(r[model])-float(r['target_integral_approx_source_power_hour']) for r in rows];rmse=math.sqrt(math.fsum(e*e for e in errors)/len(errors));mae=math.fsum(abs(e) for e in errors)/len(errors);result.append({'model':model,'n':len(errors),'RMSE':rmse,'MAE':mae,'nRMSE':rmse/REPORT_DENOMINATOR})
 return {'seed':seed,'models':result,'mode':'已开封开发段事后评分，不与2018端点任务直接比较'}
@app.get('/api/energy/export')
def energy_export(seed:int=20261026):
 rows=energy_rows(seed);buf=io.StringIO();w=csv.writer(buf);w.writerow(['origin_label','interval_end_label','forecast_integral_approx_source_power_hour','expected_low'])
 for r in rows:w.writerow([r['origin_label'],r['interval_end_label'],r['fusion_energy'],r['expected_low']])
 return Response('\ufeff'+buf.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="energy_development_{seed}.csv"'})
from storage_sandbox import solver_status,run as run_storage
class StorageInput(BaseModel):
 model_config=ConfigDict(allow_inf_nan=False,extra='forbid')
 mode:Literal['synthetic_fixture_only']='synthetic_fixture_only'
 scenario:Literal['sunny','cloud_dip','low_solar','standby','grid_pressure']='sunny'
 pv_peak_kwh:float=Field(50,ge=0,le=200)
 load_scale:float=Field(1,ge=.1,le=5)
 capacity_kwh:float=Field(60,ge=1,le=1000)
 initial_soc:float=Field(.5,ge=0,le=1)
 soc_min:float=Field(.1,ge=0,le=1)
 soc_max:float=Field(.9,ge=0,le=1)
 charge_kw:float=Field(20,ge=0,le=300)
 discharge_kw:float=Field(20,ge=0,le=300)
 grid_import_kw:float=Field(60,ge=0,le=500)
 grid_export_kw:float=Field(60,ge=0,le=500)
 eta_charge:float=Field(.95,gt=0,le=1)
 eta_discharge:float=Field(.95,gt=0,le=1)
 throughput_cost_per_kwh:float=Field(.02,ge=0,le=2)
 def checked(self):
  if not self.soc_min<=self.initial_soc<=self.soc_max:raise HTTPException(400,'初始SOC必须在SOC上下限内')
  return self.model_dump()
@app.get('/api/storage/status')
def storage_status():return solver_status()|{'contract':json.loads((ROOT/'source_records/dispatch_contract.json').read_text(encoding='utf-8'))}
@lru_cache(maxsize=16)
def cached_storage(config):return run_storage(json.loads(config))
def storage_result(body):
 if not solver_status()['available']:raise HTTPException(503,'双击install_dispatch_windows.bat安装优化依赖后重启；其他功能可继续使用。')
 try:return cached_storage(json.dumps(body.checked(),sort_keys=True))
 except (ValueError,AssertionError) as exc:raise HTTPException(400,str(exc))
@app.post('/api/storage/solve')
def storage_solve(body:StorageInput):
 result=storage_result(body);saved=RECORDS.save_plan(result);record('synthetic_storage_plan',{'record_id':saved['id'],'config_sha256':result['config_sha256'],'mode':result['mode'],'milp_status':result['strategies']['milp']['status']});return dict(result,saved_record=saved)
@app.post('/api/storage/export')
def storage_export(body:StorageInput):return Response(json.dumps(storage_result(body),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':'attachment; filename="synthetic_storage_plan.json"'})


from live_simulation import Runtime as LiveRuntime,DEFAULT as LIVE_DEFAULT
LIVE=LiveRuntime(RUNTIME/'live_runs')
from record_store import RecordStore,registry,utc_now
RECORDS=RecordStore(ROOT,RUNTIME,LIVE)
from bench_experiments import BenchManager, Busy, protocol as bench_protocol, SCENARIOS as BENCH_SCENARIOS
BENCH=BenchManager(RUNTIME/'bench_runs')

@app.get('/api/provenance')
def provenance_registry():
 with LOCK:return registry(ROOT,SETS)
@app.get('/api/provenance/{asset_id}/export')
def provenance_export(asset_id:str):
 item=next((x for x in provenance_registry()['items'] if x['id']==asset_id),None)
 if item is None:raise HTTPException(404,'数据或模型不存在')
 return Response(json.dumps({'release':'V27','captured_at_utc':utc_now(),'asset':item},ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':'attachment; filename="provenance_'+asset_id+'.json"'})
@app.get('/api/records')
def run_records():return RECORDS.list()
@app.get('/api/records/{run_id}')
def run_detail(run_id:str):
 try:return RECORDS.capture(run_id)[0]
 except FileNotFoundError:raise HTTPException(404,'运行记录不存在')
 except (OSError,ValueError,KeyError,TypeError):raise HTTPException(409,'记录不完整，无法读取；原文件保留。')
@app.get('/api/records/{run_id}/export')
def run_export(run_id:str):
 try:data=RECORDS.export(run_id)
 except FileNotFoundError:raise HTTPException(404,'运行记录不存在')
 except (OSError,ValueError,KeyError,TypeError):raise HTTPException(409,'记录不完整，无法导出；原文件保留。')
 return Response(data,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="run_{run_id}.zip"'})
class LiveConfig(BaseModel):
 model_config=ConfigDict(allow_inf_nan=False,extra='forbid')
 irradiance:float=Field(900,ge=0,le=1200)
 ambient:float=Field(25,ge=-10,le=45)
 wind:float=Field(2,ge=0,le=15)
 temperature_mode:Literal[0,1]=0
 module_temperature:float=Field(25,ge=-20,le=90)
 humidity:float=Field(50,ge=0,le=100)
 dust_optical_depth:float=Field(0,ge=0,le=2)
 soiling_pct:float=Field(0,ge=0,le=100)
 cloud_depth:float=Field(.75,ge=0,le=.95)
 cloud_width:float=Field(12,ge=3,le=30)
 cloud_speed:float=Field(.6,ge=0,le=5)
 load_kw:float=Field(30,ge=0,le=120)
 grid_import_kw:float=Field(60,ge=0,le=120)
 grid_export_kw:float=Field(60,ge=0,le=120)
 inverter_limit_kw:float=Field(60,ge=0,le=100)
 battery_enabled:bool=True
class LiveCommand(BaseModel):
 action:Literal['start','pause','reset','speed']
 speed:float=Field(1,ge=.1,le=10)
class LiveConfigure(BaseModel):
 config:LiveConfig
 reset:bool=False
from matlab_live import MatlabLive
MATLAB_GATE=threading.RLock()
MATLIVE=MatlabLive(RUNTIME/'matlab_live',ROOT/'matlab_adapter')
def start_live_engine():BENCH.activate();LIVE.start_worker();MATLAB_JOBS.start();AI.start();MATLIVE.activate()
def stop_live_engine():MATLIVE.close();AI.close();MATLAB_JOBS.stop();BENCH.close();LIVE.close()
@app.get('/api/live/state')
def live_state():return LIVE.state()
@app.post('/api/live/control')
def live_control(body:LiveCommand):return LIVE.command(body.action,body.speed)
@app.post('/api/live/config')
def live_config(body:LiveConfigure):return LIVE.configure(body.config.model_dump(),body.reset)
@app.get('/api/live/history')
def live_history(after:int=0):
 with LIVE.lock:return {'run_id':LIVE.model.run_id,'rows':LIVE.trace(after)}
@app.get('/api/live/export')
def live_export():
 with LIVE.lock:return Response(LIVE.export(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="live_run_{LIVE.model.run_id}.csv"'})
@app.websocket('/ws/simulation')
async def live_websocket(ws:WebSocket):
 await ws.accept();after=0;run_id=''
 try:
  while True:
   with LIVE.lock:
    state=LIVE.state()
    if run_id!=state['run_id']:after=0;run_id=state['run_id']
    rows=LIVE.trace(after);gap=bool(rows and rows[0]['seq']>after+1)
    if rows:after=rows[-1]['seq']
   await ws.send_json({'state':state,'rows':rows,'history_gap':gap});await asyncio.sleep(.2)
 except (WebSocketDisconnect,RuntimeError):pass

class BenchInput(BaseModel):
 model_config=ConfigDict(extra='forbid')
 scenario:Literal['cloud_pass','load_step','export_cap']
 duration_s:Literal[30,60,120]
@app.get('/experiments')
def bench_page():return FileResponse(ROOT/'static'/'bench.html')
@app.get('/api/bench/info')
def bench_info():return {'scenarios':BENCH_SCENARIOS,'durations':[30,60,120],'dt_s':.1}
@app.get('/api/bench/protocol')
def bench_preview(scenario:str='cloud_pass',duration_s:int=30):
 try:return bench_protocol(scenario,duration_s)[0]
 except ValueError as exc:raise HTTPException(400,str(exc))
@app.get('/api/bench/jobs')
def bench_jobs():return BENCH.list()
@app.post('/api/bench/jobs')
def bench_start(body:BenchInput):
 try:
  result=BENCH.start(body.scenario,body.duration_s);record('bench_start',{'id':result['id']});return result
 except Busy as exc:raise HTTPException(409,str(exc))
@app.get('/api/bench/jobs/{id}')
def bench_detail(id:str,series:bool=True):
 try:return BENCH.detail(id,series)
 except FileNotFoundError:raise HTTPException(404,'实验记录不存在')
 except (ValueError,KeyError,OSError) as exc:raise HTTPException(409,str(exc))
@app.get('/api/bench/jobs/{id}/scene')
def bench_scene(id:str):
 try:return BENCH.scene(id)
 except FileNotFoundError:raise HTTPException(404,'实验记录不存在')
 except (Busy,ValueError,KeyError,OSError) as exc:raise HTTPException(409,str(exc))
@app.get('/api/bench/jobs/{id}/diagnostics')
def bench_diagnostics(id:str):
 try:return BENCH.diagnostics(id)
 except FileNotFoundError:raise HTTPException(404,'实验记录不存在')
 except (Busy,ValueError,KeyError,OSError) as exc:raise HTTPException(409,str(exc))
@app.get('/api/bench/jobs/{id}/diagnostics/export')
def bench_diagnostics_export(id:str):
 report=bench_diagnostics(id)
 return Response(json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="{id}_diagnostics.json"'})
@app.post('/api/bench/jobs/{id}/cancel')
def bench_cancel(id:str):
 try:return BENCH.cancel(id)
 except Busy as exc:raise HTTPException(409,str(exc))
@app.get('/api/bench/jobs/{id}/export')
def bench_export(id:str):
 try:return Response(BENCH.export(id),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="{id}.zip"'})
 except FileNotFoundError:raise HTTPException(404,'实验记录不存在')
 except (Busy,ValueError,OSError,KeyError) as exc:raise HTTPException(409,str(exc))

from simulink_bridge import Bridge as SimulinkBridge, UNITS as SIM_UNITS
SIM_BRIDGE=SimulinkBridge(RUNTIME/'simulink_imports')
class SimulinkImport(BaseModel):
 model_config=ConfigDict(extra='forbid')
 title:str=Field(min_length=1,max_length=80)
 csv_text:str=Field(max_length=15_000_000)
 manifest:dict
@app.get('/integration')
def integration_page():return FileResponse(ROOT/'static'/'integration.html')
@app.get('/api/integration/info')
def integration_info():
 model=json.loads((ROOT/'static/integration/model_manifest.json').read_text(encoding='utf-8'))
 if hashlib.sha256((ROOT/'static/integration/WG_refined_station.glb').read_bytes()).hexdigest()!=model['glb_sha256']:raise HTTPException(409,'模型GLB指纹不一致')
 return {'mode':'offline_simulink_and_blender','matlab_online':False,'original_slx_available':(ROOT/'matlab_adapter/project/WG_PV_Environment.slx').is_file(),'units':SIM_UNITS,'model':model,'limits':{'samples':200000,'csv_bytes':15000000},'scope':'本页为离线结果接入与Blender资产加载；新建本机MATLAB批处理任务请进入/matlab。'}
@app.post('/api/integration/preview')
def integration_preview(body:SimulinkImport):
 try:return SIM_BRIDGE.preview(body.csv_text,body.manifest)
 except (ValueError,TypeError,KeyError) as exc:raise HTTPException(400,str(exc))
@app.post('/api/integration/import')
def integration_import(body:SimulinkImport):
 try:
  result=SIM_BRIDGE.save(body.title,body.csv_text,body.manifest);record('simulink_import',{'id':result['id']});return result
 except FileExistsError as exc:raise HTTPException(409,str(exc))
 except (ValueError,TypeError,KeyError) as exc:raise HTTPException(400,str(exc))
@app.get('/api/integration/records')
def integration_records():return SIM_BRIDGE.list()
@app.get('/api/integration/records/{id}')
def integration_record(id:str):
 try:return SIM_BRIDGE.read(id)
 except FileNotFoundError:raise HTTPException(404,'导入记录不存在')
 except (ValueError,TypeError,KeyError,OSError) as exc:raise HTTPException(409,str(exc))
@app.get('/api/integration/records/{id}/export')
def integration_export(id:str):
 try:return Response(SIM_BRIDGE.export(id),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="{id}.zip"'})
 except FileNotFoundError:raise HTTPException(404,'导入记录不存在')
 except (ValueError,TypeError,KeyError,OSError) as exc:raise HTTPException(409,str(exc))
@app.get('/api/integration/blend')
def integration_blend():return FileResponse(ROOT/'software_models/generated/WG_refined_station.blend',filename='WG_refined_station.blend')
@app.get('/api/integration/exporter')
def integration_exporter():
 import zipfile
 buffer=io.BytesIO()
 with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
  for n in ('wg_export_simulink.m','wg_export_mapping_example.m'):z.writestr(n,(ROOT/'software_models'/n).read_bytes())
 return Response(buffer.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="wg_simulink_exporter.zip"'})
def verified_preset(algorithm):
 if algorithm not in ('po','inc'):raise HTTPException(404,'预置结果不存在')
 from simulink_bridge import validate
 folder=ROOT/'data/simulink_comparison'/algorithm
 text=(folder/'signals.csv').read_bytes().decode('utf-8');source=json.loads((folder/'source.json').read_text(encoding='utf-8'))
 try:rows,summary=validate(text,source)
 except ValueError as e:raise HTTPException(409,str(e))
 return folder,source,rows,summary
@app.get('/api/integration/presets/{algorithm}')
def integration_preset(algorithm:str):
 folder,source,rows,summary=verified_preset(algorithm)
 return {'manifest':source,'rows':rows,'summary':summary}
@app.get('/api/integration/presets/{algorithm}/export')
def integration_preset_export(algorithm:str):
 import zipfile
 folder,source,rows,summary=verified_preset(algorithm);out=io.BytesIO()
 with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
  hashes={}
  for name in ('signals.csv','source.json'):
   b=(folder/name).read_bytes();hashes[name]=hashlib.sha256(b).hexdigest();z.writestr(name,b)
  z.writestr('hashes.json',json.dumps(hashes,indent=2))
 return Response(out.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="WG_verified_'+algorithm+'.zip"'})

@app.get('/api/integration/example')
def integration_example():
 return {'title':'P&O · 既有Simulink归档','manifest':json.loads((ROOT/'static/integration/archive_source.json').read_text(encoding='utf-8')),'rows':SETS['po']['rows'],'csv_url':'/assets/integration/archive_signals.csv','source':'沿用既有用户上传归档；未在此环境重新运行原始slx。'}

from paired_capture import PairedCapture
PAIRED=PairedCapture(DATA/'simulink_comparison')
def checked_pair():
 try:return PAIRED.load()
 except (ValueError,TypeError,KeyError,OSError) as exc:raise HTTPException(409,'对照来源检查失败：'+str(exc))
@app.get('/comparison')
def comparison_page():return FileResponse(ROOT/'static/comparison.html')
@app.get('/api/comparison/info')
def comparison_info():return checked_pair()['summary']
@app.get('/api/comparison/data')
def comparison_data(request:Request):
 pair=checked_pair();accepted=request.headers.get('accept-encoding','').lower()
 use_gzip=False
 for token in accepted.split(','):
  parts=[p.strip() for p in token.split(';')]
  if parts[0]!='gzip':continue
  try:quality=next((float(p[2:]) for p in parts[1:] if p.startswith('q=')),1.0)
  except ValueError:quality=0
  use_gzip=quality>0
 headers={'Vary':'Accept-Encoding','Cache-Control':'no-store'}
 if use_gzip:headers['Content-Encoding']='gzip'
 return Response(pair['gzip'] if use_gzip else pair['data'],media_type='application/json',headers=headers)
@app.get('/api/comparison/export')
def comparison_export():
 checked_pair()
 try:body=PAIRED.export()
 except (ValueError,TypeError,KeyError,OSError) as exc:raise HTTPException(409,'对照来源检查失败：'+str(exc))
 return Response(body,media_type='application/zip',headers={'Content-Disposition':'attachment; filename="wg_po_inc_source_pair.zip"'})

from matlab_jobs import MatlabJobs
MATLAB_JOBS=MatlabJobs(RUNTIME/'matlab_jobs',ROOT/'matlab_adapter',SIM_BRIDGE)
def local_task_request(request:Request):
 from urllib.parse import urlsplit
 if request.url.hostname not in ('127.0.0.1','localhost','::1') or not request.client or request.client.host not in ('127.0.0.1','::1','testclient'):raise HTTPException(403,'MATLAB任务仅允许本机访问')
 origin=request.headers.get('origin')
 if origin:
  parsed=urlsplit(origin)
  if parsed.scheme!=request.url.scheme or parsed.netloc!=request.url.netloc:raise HTTPException(403,'拒绝跨来源任务请求')
 if request.method=='POST' and request.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':raise HTTPException(415,'需要JSON任务请求')
class MatlabJobRequest(BaseModel):
 model_config={'extra':'forbid'}
 scenario:Literal['test','fast','slow','stage']='test'
 algorithm:Literal['as_saved','inc','po']='as_saved'
 client_token:str=Field(pattern=r'^[a-f0-9]{32}$')
@app.get('/matlab')
def matlab_page():return FileResponse(ROOT/'static/matlab.html')
@app.get('/api/matlab/environment')
def matlab_environment(request:Request):local_task_request(request);return MATLAB_JOBS.environment()
@app.get('/api/matlab/jobs')
def matlab_jobs(request:Request):local_task_request(request);return MATLAB_JOBS.list()
@app.post('/api/matlab/jobs',status_code=202)
def matlab_create(body:MatlabJobRequest,request:Request):
 local_task_request(request)
 try:
  with MATLAB_GATE:
   if MATLIVE.busy():raise ValueError('请先停止交互式Simulink会话，再运行整段任务')
   return MATLAB_JOBS.create(body.scenario,body.algorithm,body.client_token)
 except ValueError as exc:raise HTTPException(409,str(exc))
 except RuntimeError as exc:raise HTTPException(503,str(exc))
@app.get('/api/matlab/jobs/{id}')
def matlab_detail(id:str,request:Request):
 local_task_request(request)
 try:return MATLAB_JOBS.detail(id)
 except FileNotFoundError as exc:raise HTTPException(404,str(exc))
@app.post('/api/matlab/jobs/{id}/cancel')
def matlab_cancel(id:str,request:Request):
 local_task_request(request)
 try:return MATLAB_JOBS.cancel(id)
 except FileNotFoundError as exc:raise HTTPException(404,str(exc))
 except ValueError as exc:raise HTTPException(409,str(exc))
@app.get('/api/matlab/jobs/{id}/export')
def matlab_job_export(id:str,request:Request):
 local_task_request(request)
 try:return FileResponse(MATLAB_JOBS.export(id),filename=id+'_evidence.zip')
 except FileNotFoundError as exc:raise HTTPException(404,str(exc))
 except ValueError as exc:raise HTTPException(409,str(exc))

app.mount('/assets',StaticFiles(directory=ROOT/'static'),name='assets')
@app.get('/overview')
def overview_page():return FileResponse(ROOT/'static'/'home.html')
from local_ai import LocalAI,summarize,MODELS

def ai_snapshot(source,record_id=None):
 if source=='matlab_live':
  with MATLIVE.lock:
   state=MATLIVE.snapshot()
   if not state.get('matlab_execution_confirmed') or not MATLIVE.rows:raise ValueError('尚无已确认的MATLAB交互采样；请先启动Simulink会话')
   end=MATLIVE.rows[-1]['time_s'];rows=[dict(r) for r in MATLIVE.rows if r['time_s']>=end-1]
  return summarize(rows,'interactive_simulink',source,state['id'],{'Pgrid_W':('并网有功','kW',.001),'Qgrid_var':('并网无功','kvar',.001),'G_Wm2':('辐照','W/m²',1),'Vdc_V':('母线电压','V',1),'VdcRef_V':('电压参考','V',1)})
 if source=='live':
  with LIVE.lock:
   state=LIVE.state();rows=[dict(r) for r in LIVE.model.history if r['time_s']>=state['time_s']-60]
   if not rows:rows=[state]
  fields={'pv_ac_kw':('PV交流功率','kW',1),'load_kw':('负荷','kW',1),'mean_irradiance':('有效辐照','W/m²',1),'soc':('SOC','%',100),'temperature_C':('组件温度','℃',1),'unserved_kw':('供电缺额','kW',1),'balance_residual_kw':('功率平衡残差','kW',1)}
  return summarize(rows,'live_python',source,state['run_id'],fields)
 if source in ('po','inc'):
  folder,manifest,rows,summary=verified_preset(source);id=manifest['csv_sha256']
 elif source=='record' and record_id:
  capture=SIM_BRIDGE.read(record_id);rows=capture['rows'];id=record_id
 else:raise ValueError('请选择有效数据来源')
 return summarize(rows,'offline_simulink',source,id,{'Pgrid_W':('并网有功','kW',.001),'G_Wm2':('辐照','W/m²',1),'Vdc_V':('母线电压','V',1),'VdcRef_V':('电压参考','V',1)})
AI=LocalAI(RUNTIME/'ai',ai_snapshot)
class AIInput(BaseModel):
 model_config={'extra':'forbid'}
 kind:Literal['prepare','analysis']='analysis'
 model:Literal['deepseek-r1:1.5b','deepseek-r1:7b']='deepseek-r1:1.5b'
 source:Literal['live','matlab_live','po','inc','record']='live'
 record_id:str|None=Field(default=None,pattern=r'^sim_[0-9a-f]{20}$')
 client_token:str=Field(pattern=r'^[0-9a-f]{32}$')
@app.get('/ai')
def ai_page():return FileResponse(ROOT/'static/ai.html')
@app.get('/api/ai/status')
def ai_status():return AI.status()
@app.get('/api/ai/snapshot')
def ai_snapshot_read(source:Literal['live','matlab_live','po','inc','record']='live',record_id:str|None=None):
 try:return ai_snapshot(source,record_id)
 except (ValueError,FileNotFoundError) as e:raise HTTPException(400,str(e))
@app.get('/api/ai/jobs')
def ai_history():return AI.recent()
@app.post('/api/ai/jobs',status_code=202)
def ai_create(body:AIInput,request:Request):
 local_task_request(request)
 raise HTTPException(410,'本地模型任务已停用，请使用穹禾数字助手和DeepSeek API')
@app.get('/api/ai/jobs/{id}')
def ai_detail(id:str):
 try:return AI.read(id)
 except FileNotFoundError:raise HTTPException(404,'分析记录不存在')
@app.get('/api/ai/jobs/{id}/export')
def ai_export(id:str):
 try:result=AI.read(id)
 except FileNotFoundError:raise HTTPException(404,'分析记录不存在')
 return Response(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="{id}.json"'})
@app.get('/api/launch/info')
def launch_info(request:Request):
 local=is_local(request.client.host if request.client else '')
 info={'version':'V27','lan':ACCESS['lan'],'read_only':not local,'mode':'lan' if ACCESS['lan'] else 'local'}
 if local:
  import socket,ipaddress
  addresses=[]
  try:
   for item in socket.getaddrinfo(socket.gethostname(),None,socket.AF_INET):
    address=item[4][0];ip=ipaddress.ip_address(address)
    if ip.is_private and not ip.is_loopback and not ip.is_link_local:addresses.append(address)
  except OSError:pass
  info.update(instance_key=INSTANCE_KEY,port=ACCESS['port'],share_urls=[f"http://{ip}:{ACCESS['port']}/?access={ACCESS['token']}" for ip in sorted(set(addresses))] if ACCESS['lan'] else [])
 return info

@app.get('/story')
def story_page():return FileResponse(ROOT/'static'/'landing.html')
@app.get('/api/showcase')
def showcase_info():
 return json.loads((ROOT/'source_records/matlab_validation/verified_runs.json').read_text(encoding='utf-8'))
RESOURCE_FILES={
 'blend':ROOT/'software_models/generated/WG_refined_station.blend',
 'glb':ROOT/'static/integration/WG_refined_station.glb',
 'blender_script':ROOT/'software_models/build_station_blender.py',
 'slx':ROOT/'matlab_adapter/project/WG_PV_Environment.slx',
 'parameters':ROOT/'matlab_adapter/project/ControlThreePhaseSolarInverterParameters.m',
 'evidence_po':ROOT/'source_records/matlab_validation/po_evidence.zip',
 'evidence_inc':ROOT/'source_records/matlab_validation/inc_evidence.zip',
}
@app.get('/api/resources/package')
def resource_package():
 import zipfile
 stream=io.BytesIO()
 with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
  paths=[ROOT/'software_models/README_建模包.md',ROOT/'software_models/build_station_blender.py',ROOT/'software_models/generated/WG_refined_station.blend',ROOT/'static/integration/WG_refined_station.glb',ROOT/'static/integration/model_manifest.json']
  paths.extend(p for p in (ROOT/'matlab_adapter').rglob('*') if p.is_file() and not any(n in p.parts for n in ('runs','__pycache__')))
  hashes={}
  for p in paths:
   n=p.relative_to(ROOT).as_posix();b=p.read_bytes();hashes[n]=hashlib.sha256(b).hexdigest();z.writestr(n,b)
  z.writestr('SHA256.json',json.dumps(hashes,indent=2))
 return Response(stream.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="WG_complete_models.zip"'})
@app.get('/api/resources/{key}')
def resource_file(key:str):
 path=RESOURCE_FILES.get(key)
 if not path:raise HTTPException(404,'资源不存在')
 return FileResponse(path,filename=path.name)

@app.get('/')
def index():return FileResponse(ROOT/'static'/'landing.html')
@app.get('/platform')
def platform():return FileResponse(ROOT/'static'/'index.html')
def available_local_port(preferred=8766,host='127.0.0.1'):
 import socket
 for port in range(preferred,preferred+20):
  with socket.socket() as probe:
   try:probe.bind((host,port));return port
   except OSError:continue
 raise RuntimeError('No free local port in the platform startup range.')
# Interaction endpoints deliberately separate new solver data from archived captures.
class MatlabLiveValues(BaseModel):
 model_config={'extra':'forbid'}
 irradiance:float=Field(1000,ge=0,le=1200)
 iq_ref_pu:float=Field(0,ge=-.3,le=.3)
 cloud:bool=False
class MatlabLiveConfig(MatlabLiveValues):
 session_id:str=Field(pattern=r'^ml_[0-9a-f]{32}$')
class MatlabLiveSession(MatlabLiveValues):
 algorithm:Literal['po','inc']='po'
 client_token:str=Field(pattern=r'^[a-f0-9]{32}$')
class MatlabLiveControl(BaseModel):
 model_config={'extra':'forbid'}
 session_id:str=Field(pattern=r'^ml_[0-9a-f]{32}$')
 action:Literal['start','pause','step','stop']
@app.get('/matlab-live')
def matlab_live_page():return FileResponse(ROOT/'static/matlab_live.html')
@app.get('/api/matlab-live/state')
def matlab_live_state():return {'environment':MATLIVE.environment(),'state':MATLIVE.snapshot()}
@app.get('/api/matlab-live/data')
def matlab_live_data(after:float=-1):
 try:return MATLIVE.data(after)
 except ValueError as e:raise HTTPException(400,str(e))
@app.post('/api/matlab-live/session',status_code=202)
def matlab_live_new(body:MatlabLiveSession,request:Request):
 local_task_request(request)
 try:
  with MATLAB_GATE:
   if any(j['status'] not in ('completed','captured','failed','cancelled','interrupted') for j in MATLAB_JOBS.list()):raise ValueError('请先结束或取消整段MATLAB任务')
   id=MATLIVE.create(body.algorithm,body.model_dump(include={'irradiance','iq_ref_pu','cloud'}),body.client_token)
   return {'id':id,'state':MATLIVE.snapshot() if MATLIVE.snapshot()['id']==id else json.loads((MATLIVE.root/id/'state.json').read_text(encoding='utf-8'))}
 except ValueError as e:raise HTTPException(409,str(e))
 except RuntimeError as e:raise HTTPException(503,str(e))
@app.post('/api/matlab-live/config')
def matlab_live_config(body:MatlabLiveConfig,request:Request):
 local_task_request(request)
 try:return MATLIVE.configure(body.model_dump(exclude={'session_id'}),body.session_id)
 except (ValueError,RuntimeError) as e:raise HTTPException(409,str(e))
@app.post('/api/matlab-live/control')
def matlab_live_command(body:MatlabLiveControl,request:Request):
 local_task_request(request)
 try:return MATLIVE.command(body.action,body.session_id)
 except (ValueError,RuntimeError) as e:raise HTTPException(409,str(e))
@app.get('/api/matlab-live/{id}/export')
def matlab_live_export(id:str):
 try:data=MATLIVE.export(id)
 except FileNotFoundError as e:raise HTTPException(404,str(e))
 return Response(data,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="{id}.zip"'})

# Cloud assistant is independent from legacy local-model jobs.
from deepseek_api import DeepSeekAssistant,MODELS as CLOUD_MODELS,now as assistant_now

def assistant_context(source='auto'):
 state=MATLIVE.snapshot()
 actual='matlab_live' if source=='auto' and state.get('matlab_execution_confirmed') and state.get('latest') else 'live' if source=='auto' else source
 result={'captured_at_utc':assistant_now(),'requested_source':source,'source':actual,'available':False,'metrics':[],
  'limits':{'Simulink_irradiance_Wm2':[0,1200],'Simulink_iq_ref_pu':[-.3,.3],'temperature_C':25,'algorithm_change':'new session only','explicit_assistant_setpoints':True},
  'forecast':forecast_info(),'website':{'name':'稳供智诊','scope':'光伏物理仿真、三维场站与历史预测研究','real_station_connected':False}}
 if actual=='matlab_live':
  result['simulation']={k:state.get(k) for k in ('id','phase','running_requested','revision','applied_revision','config','applied_config','applied_time_s','updated_at','sample_count','matlab_execution_confirmed')}
  result['display_label']='Simulink 原模型交互求解'
 elif actual=='live':
  with LIVE.lock:st=LIVE.state()
  result['simulation']={k:st.get(k) for k in ('run_id','time_s','running','playing','status','config','error','backlog_sim_seconds')}
  result['display_label']='Python 简化模拟（不是 MATLAB 求解）'
  if source=='auto':result['selection_note']='尚无已确认的MATLAB交互采样，自动选择Python简化模拟；不得当作原SLX输出'
 else:result['display_label']='Simulink 归档记录（非实时求解）'
 try:
  snapshot=ai_snapshot(actual);result.update(available=True,metrics=snapshot['metrics'],window={'start_s':snapshot['start_s'],'end_s':snapshot['end_s'],'samples':snapshot['samples']},snapshot_sha256=snapshot['snapshot_sha256'],rules=snapshot.get('rule_findings',[]))
 except (ValueError,FileNotFoundError) as e:result['unavailable_reason']=str(e)
 return result

from assistant_controls import LABELS, describe_setpoints, parse_setpoints

def assistant_control(question,source,page):
 patch=parse_setpoints(question)
 if patch is None:return None
 if source in ('po','inc'):raise ValueError('归档记录只能查看。请选择实时虚拟仿真或 Simulink 交互求解。')
 target=source if source!='auto' else ('matlab_live' if page.split('?')[0]=='/matlab-live' else 'live')
 changes=describe_setpoints(patch)
 if target=='matlab_live':
  unsupported=patch.keys()-{'irradiance','iq_ref_pu','cloud'}
  if unsupported:raise ValueError('Simulink 交互页支持辐照、无功电流参考与云团扰动。其他环境参数请选择实时虚拟仿真。')
  with MATLIVE.lock:
   state=MATLIVE.snapshot()
   try:values=MatlabLiveValues.model_validate({**(state.get('config') or {}),**patch}).model_dump()
   except ValueError:raise ValueError('参数超出范围：辐照 0–1200 W/m²，无功电流参考 −0.3–0.3 pu。')
   applied=MATLIVE.configure(values,state['id'])
  status='已提交；求解器将于下一计算段读取。' if applied['running_requested'] else '已提交；模型就绪后点「开始求解」或「单步」生效。'
  return f'Simulink · {changes}。{status}'
 unsupported=patch.keys()-LiveConfig.model_fields.keys()
 if unsupported:raise ValueError('该指令适用于 Simulink 交互求解。请切换对话数据源后重试。')
 with LIVE.lock:
  try:values=LiveConfig.model_validate({**LIVE.model.config,**patch}).model_dump()
  except ValueError as error:
   field=error.errors()[0]['loc'][0]
   bounds=next((item for item in LiveConfig.model_fields[field].metadata if hasattr(item,'ge')),None)
   upper=next((item for item in LiveConfig.model_fields[field].metadata if hasattr(item,'le')),None)
   detail=f'（{bounds.ge:g}–{upper.le:g}）' if bounds is not None and upper is not None else ''
   raise ValueError(f'{LABELS[field]}超出允许范围{detail}。本条指令未修改参数。')
  LIVE.configure(values)
  running=LIVE.running
 status='已提交；从下一模拟步起生效。' if running else '已提交；点「启动仿真」后生效。'
 return f'实时虚拟仿真 · {changes}。{status}'

ASSISTANT=DeepSeekAssistant(ROOT,RUNTIME,assistant_context,control=assistant_control)
class AssistantSettings(BaseModel):
 model_config={'extra':'forbid'}
 api_key:str|None=Field(default=None,max_length=256)
 model:Literal['deepseek-flash','deepseek-v4-pro']='deepseek-flash'
class AssistantQuestion(BaseModel):
 model_config={'extra':'forbid'}
 question:str=Field(min_length=1,max_length=2000)
 source:Literal['auto','matlab_live','live','po','inc']='auto'
 session_id:str=Field(pattern=r'^[0-9a-f]{32}$')
 client_token:str=Field(pattern=r'^[0-9a-f]{32}$')
 page:str=Field(default='/ai',max_length=150,pattern=r'^/[a-zA-Z0-9_/?=&#.\-]*$')
@app.get('/api/assistant/status')
def assistant_status():return ASSISTANT.status()
@app.get('/api/assistant/context')
def assistant_context_read(source:Literal['auto','matlab_live','live','po','inc']='auto'):return assistant_context(source)
@app.post('/api/assistant/settings')
def assistant_settings(body:AssistantSettings,request:Request):
 local_task_request(request)
 try:return ASSISTANT.configure(body.api_key,body.model)
 except ValueError as e:raise HTTPException(400,str(e))
 except RuntimeError as e:raise HTTPException(409,str(e))
@app.post('/api/assistant/messages',status_code=202)
def assistant_message(body:AssistantQuestion,request:Request):
 local_task_request(request)
 if not body.question.strip():raise HTTPException(422,'问题不能为空')
 try:return ASSISTANT.create(body.question.strip(),body.source,body.session_id,body.client_token,body.page)
 except ValueError as e:raise HTTPException(400,str(e))
 except RuntimeError as e:raise HTTPException(409,str(e))
@app.get('/api/assistant/conversations/{session_id}')
def assistant_conversation_read(session_id:str):
 if len(session_id)!=32 or any(c not in '0123456789abcdef' for c in session_id):raise HTTPException(422,'会话标识无效')
 return ASSISTANT.conversation(session_id)
@app.get('/api/assistant/messages/{id}')
def assistant_message_read(id:str):
 try:return ASSISTANT.read(id)
 except FileNotFoundError as e:raise HTTPException(404,str(e))

if __name__=='__main__':
 import uvicorn,webbrowser,argparse,secrets
 parser=argparse.ArgumentParser();parser.add_argument('--lan',action='store_true');args=parser.parse_args()
 host='0.0.0.0' if args.lan else '127.0.0.1';port=available_local_port(host=host);url=f'http://127.0.0.1:{port}'
 ACCESS.update(lan=args.lan,token=secrets.token_urlsafe(32) if args.lan else '',port=port)
 print(f'Platform URL: {url}',flush=True)
 if args.lan:print('LAN read-only sharing enabled. Open Smart Analysis > Access to copy the full share URL. Allow this application on PRIVATE networks if Windows asks.',flush=True)
 def open_when_ready():
  import urllib.request
  for _ in range(60):
   try:
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url+'/api/health',timeout=1) as r:
     if r.status==200:webbrowser.open(url);return
   except OSError:time.sleep(.25)
 threading.Thread(target=open_when_ready,daemon=True).start();uvicorn.run(app,host=host,port=port,proxy_headers=False)

