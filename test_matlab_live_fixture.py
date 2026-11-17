"""Child-process fixture, not a MATLAB solver. Only used by test_matlab_live.py."""
from pathlib import Path
import csv,hashlib,json,os,time
folder=Path(os.environ['WG_MATLAB_LIVE_DIR']);req=json.loads((folder/'session.json').read_text());mode=os.environ.get('WG_LIVE_FIXTURE_MODE','normal')
def write(name,p):
 f=folder/name;t=f.with_suffix('.tmp');t.write_text(json.dumps(p),encoding='utf-8');t.replace(f)
write('ready.json',{'id':req['id'],'schema':'wg-matlab-live-v1','producer':'wg_live_entry','model_sha256':req['model_sha256'],'Ts':50e-6,'release':'TEST_FIXTURE','command_transport':req['command_transport']})
if mode=='idle_close':
 time.sleep(.12)
 write('closed.json',{'id':req['id'],'status':'closed','reason':'idle_timeout'})
 raise SystemExit(0)
if mode in ('exit_error','exit_unreported'):
 time.sleep(.12)
 if mode=='exit_error':
  write('failure.json',{'identifier':'WG:FixtureFailure','message':'fixture MATLAB error'})
 raise SystemExit(31)
seq=0;tick=0;previous=req["irradiance"]
while True:
 p=folder/f'command_{seq+1:08d}.json'
 if not p.exists():time.sleep(.01);continue
 cmd=json.loads(p.read_text())
 assert cmd['seq']==seq+1
 seq=cmd['seq']
 if cmd['action']=='stop':break
 if mode=='timeout':time.sleep(5);continue
 if mode=='slow':time.sleep(.25)
 start=tick*50e-6;name=f'chunk_{seq:08d}.csv';path=folder/name
 with path.open('w',newline='') as f:
  writer=csv.writer(f);writer.writerow(['time_s','G_Wm2','Pgrid_W','Qgrid_var','Vdc_V','VdcRef_V'])
  changed=previous!=cmd['irradiance']
  for k in range(2000):
   g=cmd['irradiance']
   if mode=='sample_boundary':g=previous if changed and k==0 else g+0.00003
   if mode=='stale_irradiance':g=previous
   writer.writerow([(tick+k)*50e-6,g,1000*seq+g,cmd['iq_ref_pu']*1000,850+seq,850])
 previous=cmd['irradiance']
 tick+=2000
 reply={'id':req['id'],'schema':'wg-matlab-live-v1','producer':'wg_live_entry','seq':seq,'revision':cmd['revision'],'csv':name,'csv_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'samples':2000,'solver_time_s':tick*50e-6,'applied_time_s':start,'final':tick>=600000}
 if mode=='sample_boundary' and changed:reply['irradiance_observed_time_s']=start+50e-6
 if mode=='wrong_sequence':reply['seq']+=1
 if mode=='bad_hash':reply['csv_sha256']='0'*64
 write(f'reply_{seq:08d}.json',reply)
