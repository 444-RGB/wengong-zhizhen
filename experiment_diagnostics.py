"""Deterministic operating-event rules for synthetic experiments; no fault inference."""
import hashlib,json,math
GROUPS=('without_battery','with_battery')
VERSION='operating-events-v1'
RULES=[
 {'id':'unserved','title':'供电缺口','metric':'unserved_kw','operator':'>','threshold':1e-7,'unit':'kW','battery_only':False,'meaning':'未供电功率大于数值容差；需同时查看负荷、光伏和电网进口限额。'},
 {'id':'curtail','title':'弃光','metric':'curtail_kw','operator':'>','threshold':1e-7,'unit':'kW','battery_only':False,'meaning':'模型已计算弃光功率；需核对负荷、充电功率及电网出口限额。'},
 {'id':'charge_limit','title':'充电功率达到上限','metric':'charge_kw','operator':'>=','threshold':20-1e-6,'unit':'kW','battery_only':True,'meaning':'接近固定20 kW充电上限，属于运行约束，不等于设备故障。'},
 {'id':'discharge_limit','title':'放电功率达到上限','metric':'discharge_kw','operator':'>=','threshold':20-1e-6,'unit':'kW','battery_only':True,'meaning':'接近固定20 kW放电上限，属于运行约束，不等于设备故障。'},
 {'id':'soc_lower','title':'SOC达到下边界','metric':'soc','operator':'<=','threshold':.1+1e-8,'unit':'%','battery_only':True,'meaning':'接近固定10% SOC下边界；需核对充放电及存储能量。'},
 {'id':'soc_upper','title':'SOC达到上边界','metric':'soc','operator':'>=','threshold':.9-1e-8,'unit':'%','battery_only':True,'meaning':'接近固定90% SOC上边界；需核对充放电及存储能量。'}]
def canonical(v):return json.dumps(v,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
def rule_protocol():
 return {'version':VERSION,'rules':RULES,'rules_sha256':hashlib.sha256(canonical(RULES)).hexdigest(),'time_semantics':'样本k代表[(k-1)dt,k·dt]执行后的状态；事件覆盖这些相邻积分区间；初始dt=0行不参与事件。','merge':'只合并相邻触发样本，不跨越未触发样本；保留单步事件，无平滑、去噪或最短时长过滤。','scope':'合成仿真的运行规则解释；数值容差不是现场告警阈值，不推断设备故障或因果关系。'}
def is_hit(rule,row):
 value=row[rule['metric']];t=rule['threshold']
 return value>t if rule['operator']=='>' else value>=t if rule['operator']=='>=' else value<=t
FIELDS=('seq','time_s','dt_s','pv_ac_kw','pv_dc_available_kw','load_kw','charge_kw','discharge_kw','grid_import_kw','grid_export_kw','curtail_kw','unserved_kw','soc','battery_energy_kwh','temperature_C','mean_irradiance','balance_residual_kw','battery_residual_kwh')
INPUT_FIELDS=('step','start_s','end_s','irradiance','ambient','wind','cloud_depth','cloud_width','cloud_speed','load_kw','grid_import_kw','grid_export_kw','inverter_limit_kw')
def validate(spec,series,inputs):
 n=spec['steps'];dt=spec['dt_s']
 if n<1 or not math.isfinite(dt) or dt<=0 or len(inputs)!=n:raise ValueError('诊断输入长度或步长无效')
 for group in GROUPS:
  rows=series[group]
  if len(rows)!=n+1:raise ValueError('诊断轨迹不完整')
  for i,r in enumerate(rows):
   if any(k not in r or not math.isfinite(r[k]) for k in FIELDS):raise ValueError('诊断轨迹含非有限或缺失值')
   if r['seq']!=i or abs(r['time_s']-i*dt)>1e-7 or abs(r['dt_s']-(dt if i else 0))>1e-10:raise ValueError('诊断轨迹时钟不一致')
 for i,r in enumerate(inputs,1):
  if any(k not in r or not math.isfinite(r[k]) for k in INPUT_FIELDS):raise ValueError('诊断输入含非有限或缺失值')
  if r['step']!=i or abs(r['start_s']-(i-1)*dt)>1e-7 or abs(r['end_s']-i*dt)>1e-7:raise ValueError('诊断输入时钟不一致')
  for group in GROUPS:
   if abs(series[group][i]['load_kw']-r['load_kw'])>1e-8:raise ValueError('诊断负荷与输入不一致')
def analyse(job_id,spec,series,inputs,source_sha256,numeric_sha256):
 validate(spec,series,inputs);events=[];summaries={}
 for group in GROUPS:
  rows=series[group];by_rule={}
  for rule in RULES:
   spans=[];first=None
   for i in range(1,len(rows)+1):
    hit=i<len(rows) and (group=='with_battery' or not rule['battery_only']) and is_hit(rule,rows[i])
    if hit and first is None:first=i
    if not hit and first is not None:spans.append((first,i-1));first=None
   rule_events=[]
   for no,(a,b) in enumerate(spans,1):
    reducer=min if rule['operator']=='<=' else max
    peak=reducer(range(a,b+1),key=lambda i:rows[i][rule['metric']]);r=rows[peak];scale=100 if rule['unit']=='%' else 1
    event={'id':f'{group}:{rule["id"]}:{no:03d}','group':group,'rule_id':rule['id'],'title':rule['title'],'metric':rule['metric'],'unit':rule['unit'],'start_seq':a,'end_seq':b,'peak_seq':peak,'start_s':round(rows[a]['time_s']-rows[a]['dt_s'],9),'first_sample_s':round(rows[a]['time_s'],9),'end_s':round(rows[b]['time_s'],9),'peak_s':round(r['time_s'],9),'duration_s':round(sum(x['dt_s'] for x in rows[a:b+1]),9),'sample_count':b-a+1,'peak_value':r[rule['metric']]*scale,'energy_kwh':sum(x[rule['metric']]*x['dt_s']/3600 for x in rows[a:b+1]) if rule['unit']=='kW' else None,'meaning':rule['meaning'],'evidence':{'sample':{k:r[k] for k in FIELDS},'counterpart':{k:series[GROUPS[1] if group==GROUPS[0] else GROUPS[0]][peak][k] for k in FIELDS},'input':{k:inputs[peak-1][k] for k in INPUT_FIELDS}}}
    rule_events.append(event);events.append(event)
   by_rule[rule['id']]={'events':len(rule_events),'duration_s':round(sum(e['duration_s'] for e in rule_events),9),'energy_kwh':sum(e['energy_kwh'] for e in rule_events) if rule['unit']=='kW' else None}
  summaries[group]={'event_count':sum(v['events'] for v in by_rule.values()),'by_rule':by_rule,'final_soc':rows[-1]['soc'],'final_battery_kwh':rows[-1]['battery_energy_kwh']}
 events.sort(key=lambda e:(e['start_seq'],GROUPS.index(e['group']),e['rule_id']))
 return {'id':job_id,'schema':VERSION,'protocol':rule_protocol(),'source_sha256':source_sha256,'numeric_sha256':numeric_sha256,'summary':summaries,'events':events,'terminal_note':'两组初始状态相同，末端SOC不约束相同；事件减少不能直接解释为公平经济性收益。','energy_note':'同组不同规则可能重叠；不可把各规则时长或能量相加为总损失。功率上限事件能量是充/放电吞吐量，SOC事件没有kWh积分。'}
