"""Read-only, source-checked paired offline Simulink captures; no resampling."""
from pathlib import Path
import json,hashlib,threading,gzip,io,zipfile
from simulink_bridge import validate,UNITS

class PairedCapture:
 def __init__(self,root):self.root=Path(root);self.lock=threading.RLock();self.cache=None;self.key=None
 def load(self):
  with self.lock:
   files={f'{a}/{n}':(self.root/a/n).read_bytes() for a in ('po','inc') for n in ('signals.csv','source.json')}
   hashes={n:hashlib.sha256(b).hexdigest() for n,b in files.items()};key=tuple(hashes.values())
   if self.key==key:return self.cache
   manifests={a:json.loads(files[a+'/source.json'].decode('utf-8-sig')) for a in ('po','inc')}
   records={a:validate(files[a+'/signals.csv'].decode('utf-8'),manifests[a])[0] for a in ('po','inc')}
   run={a:manifests[a].get('run') for a in ('po','inc')}
   if not all(isinstance(r,dict) for r in run.values()):raise ValueError('缺少原运行来源信息')
   required={'model_sha256','parameters_sha256','input_sha256','scenario','mPPT','Ts','solver','stop_time_s','input_interpolation','algorithm_run'}
   if not all(required<=r.keys() for r in run.values()):raise ValueError('缺少可比性字段')
   diffs=sorted(k for k in set(run['po'])|set(run['inc']) if run['po'].get(k)!=run['inc'].get(k))
   if diffs!=['algorithm_run']:raise ValueError('两组不满足仅算法不同的对照条件：'+','.join(diffs))
   if run['po']['algorithm_run']!='Perturb and observe (P&O)' or run['inc']['algorithm_run']!='Incremental conductance (INC)':raise ValueError('算法标签与运行来源不符')
   left,right=records['po'],records['inc']
   if len(left)!=len(right) or any(a['time_s']!=b['time_s'] or a['G_Wm2']!=b['G_Wm2'] for a,b in zip(left,right)):raise ValueError('两组保存时钟或辐照不完全一致，拒绝隐藏对齐')
   metrics={}
   for name,rows in records.items():
    restored=[r for r in rows if 2.5<=r['time_s']<=4]
    if not restored or rows[-1]['time_s']!=4:raise ValueError('预置test对照需要0—4秒完整记录')
    energy=sum((a['Pgrid_W']+b['Pgrid_W'])*.5*(b['time_s']-a['time_s']) for a,b in zip(rows,rows[1:]))/3600
    metrics[name]={'samples':len(rows),'energy_Wh':energy,'restore_v_min_V':min(r['Vdc_V'] for r in restored),'restore_v_max_V':max(r['Vdc_V'] for r in restored)}
   summary={'schema':'wg-paired-capture-v1','duration_s':left[-1]['time_s'],'samples':len(left),'units':UNITS,'metrics':metrics,'energy_change_pct':(metrics['inc']['energy_Wh']/metrics['po']['energy_Wh']-1)*100,'window_s':[2.5,4],'metadata_differences':diffs,'clock_equal':True,'irradiance_equal':True,'files':hashes,'sources':manifests,'scope':'用户本机Simulink导出，双记录离线回放；本服务未执行MATLAB。单一4秒工况，不代表MPPT效率、全年收益或设备故障。120网格仅示意资产，原电路24块。'}
   columns=list(UNITS)[1:]
   data={'time_s':[r['time_s'] for r in left],'columns':columns,**{a:[[r[k] for k in columns] for r in rows] for a,rows in records.items()}}
   encoded=json.dumps(data,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
   self.cache={'summary':summary,'data':encoded,'gzip':gzip.compress(encoded,compresslevel=3),'files':files};self.key=key
   return self.cache
 def export(self):
  capture=self.load();out=io.BytesIO()
  with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
   for n,b in capture['files'].items():z.writestr(n,b)
   z.writestr('comparison.json',json.dumps(capture['summary'],ensure_ascii=False,indent=2))
   z.writestr('hashes.json',json.dumps(capture['summary']['files'],indent=2))
  return out.getvalue()
