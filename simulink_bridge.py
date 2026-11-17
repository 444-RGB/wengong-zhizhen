"""Strict offline CSV/manifest bridge. Never invokes MATLAB or resamples data."""
import csv,hashlib,io,json,math,re,threading,zipfile
from pathlib import Path
from datetime import datetime,timezone
UNITS={'time_s':'s','G_Wm2':'W/m2','Pgrid_W':'W','Qgrid_var':'var','Vdc_V':'V','VdcRef_V':'V'}
COLUMNS=list(UNITS)
def sha(b):return hashlib.sha256(b).hexdigest()
def encode(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,allow_nan=False,indent=2).encode('utf-8')
def validate(csv_text,manifest):
 raw=csv_text.encode('utf-8')
 if len(raw)>15_000_000:raise ValueError('CSV最多15 MB')
 if manifest.get('schema')!='wg-simulink-export-v1' or manifest.get('units')!=UNITS:raise ValueError('需要wg-simulink-export-v1清单及明确的六列标准单位；本接口不猜测单位或自动换算')
 if manifest.get('csv_sha256')!=sha(raw):raise ValueError('CSV与清单SHA256不一致；请选择同次导出的两个文件')
 if not isinstance(manifest.get('model_name'),str) or not 1<=len(manifest['model_name'])<=160:raise ValueError('清单缺少有效模型名')
 if not isinstance(manifest.get('signals'),dict) or set(manifest['signals'])!=set(COLUMNS)-{'time_s'}:raise ValueError('清单需要五个信号的来源映射')
 for signal,item in manifest['signals'].items():
  if not isinstance(item,dict) or not isinstance(item.get('source_name'),str) or not item['source_name'] or not isinstance(item.get('scale'),(int,float)) or isinstance(item.get('scale'),bool) or not math.isfinite(item['scale']) or item['scale']<=0:raise ValueError('信号来源名或显式缩放系数无效：'+signal)
 reader=csv.DictReader(io.StringIO(csv_text.lstrip('\ufeff')))
 if reader.fieldnames!=COLUMNS:raise ValueError('CSV列名和顺序必须为 '+','.join(COLUMNS))
 rows=[]
 for line,r in enumerate(reader,2):
  if None in r:raise ValueError(f'第{line}行有多余列')
  try:v={k:float(r[k]) for k in COLUMNS}
  except (TypeError,ValueError):raise ValueError(f'第{line}行含非数值或缺失信号')
  if not all(math.isfinite(x) for x in v.values()) or v['time_s']<0 or v['G_Wm2']<0:raise ValueError(f'第{line}行含非有限值或负时间/辐照')
  if rows and v['time_s']<=rows[-1]['time_s']:raise ValueError(f'第{line}行时间不严格递增；不自动合并重复采样')
  rows.append(v)
  if len(rows)>200000:raise ValueError('最多200000个样本；本接口不删除或重采样')
 if len(rows)<2 or rows[0]['time_s']!=0:raise ValueError('至少两行，时间从0秒开始')
 if manifest.get('sample_count')!=len(rows):raise ValueError('清单样本数与CSV不符')
 dt=[b['time_s']-a['time_s'] for a,b in zip(rows,rows[1:])]
 return rows,{'samples':len(rows),'duration_s':rows[-1]['time_s'],'dt_min_s':min(dt),'dt_max_s':max(dt),'csv_sha256':sha(raw),'energy_Wh':sum((a['Pgrid_W']+b['Pgrid_W'])*.5*(b['time_s']-a['time_s']) for a,b in zip(rows,rows[1:]))/3600,'units':UNITS,'model_name':manifest['model_name'],'scope':'离线仿真导出回放；单位与来源为导出者声明，哈希只核对配对与一致性，未独立认证原始slx或MATLAB运行。'}
class Bridge:
 def __init__(self,directory):self.root=Path(directory);self.root.mkdir(parents=True,exist_ok=True);self.lock=threading.RLock()
 def preview(self,text,manifest):return validate(text,manifest)[1]
 def save(self,title,text,manifest):
  rows,summary=validate(text,manifest)
  if not isinstance(title,str) or not 1<=len(title.strip())<=80:raise ValueError('名称需要1—80个字符')
  key='sim_'+sha(text.encode()+encode(manifest))[:20];p=self.root/key
  with self.lock:
   if p.exists():raise FileExistsError('相同CSV和清单已导入，记录 '+key)
   temp=self.root/(key+'.tmp');temp.mkdir(exist_ok=False)
   try:
    (temp/'signals.csv').write_bytes(text.encode('utf-8'));(temp/'source.json').write_bytes(encode(manifest))
    meta={'id':key,'title':title.strip(),'created_at_utc':datetime.now(timezone.utc).isoformat(),'summary':summary,'files':{n:sha((temp/n).read_bytes()) for n in ('signals.csv','source.json')}}
    (temp/'record.json').write_bytes(encode(meta));temp.rename(p)
   except Exception:
    import shutil;shutil.rmtree(temp,ignore_errors=True);raise
  return meta
 def _path(self,id):
  if not re.fullmatch(r'sim_[a-f0-9]{20}',id):raise FileNotFoundError(id)
  p=self.root/id
  if not (p/'record.json').is_file():raise FileNotFoundError(id)
  return p
 def list(self):
  items=[]
  for p in self.root.glob('sim_*/record.json'):
   try:items.append(json.loads(p.read_text(encoding='utf-8')))
   except (ValueError,OSError):continue
  return sorted(items,key=lambda x:x['created_at_utc'],reverse=True)
 def read(self,id):
  p=self._path(id);meta=json.loads((p/'record.json').read_text(encoding='utf-8'))
  for n,h in meta['files'].items():
   if n not in ('signals.csv','source.json') or sha((p/n).read_bytes())!=h:raise ValueError('导入记录文件校验失败')
  text=(p/'signals.csv').read_bytes().decode('utf-8');source=json.loads((p/'source.json').read_text(encoding='utf-8'));rows,summary=validate(text,source)
  return {'record':meta,'manifest':source,'rows':rows}
 def export(self,id):
  self.read(id);p=self._path(id);out=io.BytesIO()
  with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
   names=['record.json','source.json','signals.csv']
   for n in names:z.writestr(n,(p/n).read_bytes())
   z.writestr('hashes.json',encode({n:sha((p/n).read_bytes()) for n in names}))
  return out.getvalue()
