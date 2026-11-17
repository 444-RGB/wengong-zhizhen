"""Wall-paced backend reduced-order PV/thermal/inverter/battery simulation.
Independent of browsers and archived CSVs. Synthetic parameters, not EMT/HIL.
"""
import math,time,threading,uuid,csv,json,io,hashlib
from datetime import datetime,timezone
from pathlib import Path
from collections import deque
from pv_physics import parameters,voltage_at_current
DEFAULT={'irradiance':900.,'ambient':25.,'wind':2.,'temperature_mode':0,'module_temperature':25.,'humidity':50.,'dust_optical_depth':0.,'soiling_pct':0.,'cloud_depth':.75,'cloud_width':12.,'cloud_speed':.6,'load_kw':30.,'grid_import_kw':60.,'grid_export_kw':60.,'inverter_limit_kw':60.,'battery_enabled':True}
FIELDS=['seq','time_s','pv_dc_available_kw','pv_ac_kw','load_kw','charge_kw','discharge_kw','grid_import_kw','grid_export_kw','curtail_kw','unserved_kw','soc','temperature_C','mean_irradiance','balance_residual_kw','battery_residual_kwh','battery_energy_kwh']
MODEL={'name':'six-zone-reduced-dynamic-v2','step_s':.1,'electrical':'6 independently ideal-MPPT strings, 20 uniformly illuminated equivalent modules per string; no intra-string mismatch','thermal':'Faiman equilibrium plus assumed first-order lag tau=120s, or fixed module temperature','optical':'POA input times cloud transmission, exp(-dust_optical_depth*(1+0.5*humidity/100)), and (1-soiling_pct/100); scenario parameters, not calibrated weather retrieval','inverter':'eta=.97, command lag tau=.3s, availability and power clipping; no switching/DC-link ODE','battery':'60kWh,20kW,eta_charge=eta_discharge=.95,SOC .1-.9,initial .5; local surplus/deficit rule','scope':'best-effort wall-paced reduced-order numerical simulation, not hard-real-time/Simulink co-simulation'}

def module_mpp(g,t):
 if g<1e-8:return 0.,0.
 par=parameters(g,t);lo=0.;hi=par[0];ratio=(math.sqrt(5)-1)/2
 a=hi-ratio*(hi-lo);b=lo+ratio*(hi-lo)
 def power(i):return max(0.,voltage_at_current(i,par))*i
 pa,pb=power(a),power(b)
 for _ in range(26):
  if pa>pb:hi=b;b=a;pb=pa;a=hi-ratio*(hi-lo);pa=power(a)
  else:lo=a;a=b;pa=pb;b=lo+ratio*(hi-lo);pb=power(b)
 i=(lo+hi)/2;v=voltage_at_current(i,par);return max(0.,i*v),v

class Model:
 def __init__(self,config=None):self.reset(config or DEFAULT)
 def reset(self,config):
  self.config={**DEFAULT,**config};self.t=0.;self.seq=0;self.temperature=[self.config['ambient']]*6;self.inverter=0.;self.energy=30.;self.run_id=uuid.uuid4().hex[:12];self.history=deque(maxlen=36000);self.last={};self._calculate(0.)
 def _calculate(self,dt):
  c=self.config;cloud_x=-55+c['cloud_speed']*self.t;irr=[];zone_g=[];mpp=[];vmpp=[]
  # Scenario-only linear humidity enhancement of dust optical depth.
  dust_transmission = math.exp(-c['dust_optical_depth'] * (1 + 0.5 * c['humidity'] / 100))
  soiling_transmission = 1 - c['soiling_pct'] / 100
  optical_irradiance = c['irradiance'] * dust_transmission * soiling_transmission
  for si in range(6):
   xs=[-38+(si%3)*19+5.8+(k-2)*1.29 for _ in range(4) for k in range(5)]
   values=[optical_irradiance*(1-c['cloud_depth']*math.exp(-.5*((x-cloud_x)/c['cloud_width'])**2)) for x in xs];g=sum(values)/20;irr.append(values);zone_g.append(g)
   if c['temperature_mode'] == 1:
    self.temperature[si] = c['module_temperature']
   else:
    equilibrium = c['ambient'] + g / (25 + 6.84 * c['wind'])
    self.temperature[si] = equilibrium + (self.temperature[si] - equilibrium) * math.exp(-dt / 120)
   p,v=module_mpp(g,self.temperature[si]);mpp.append(20*p/1000);vmpp.append(20*v)
  available=sum(mpp)*.97;target=min(available,c['inverter_limit_kw']);self.inverter=min(target,target+(self.inverter-target)*math.exp(-dt/.3))
  supply=max(0.,self.inverter);load=c['load_kw'];charge=discharge=0.;old_energy=self.energy
  if dt>0 and c['battery_enabled']:
   if supply>load:charge=min(supply-load,20.,max(0.,54-self.energy)*3600/(.95*dt))
   else:discharge=min(load-supply,20.,max(0.,self.energy-6)*.95*3600/dt)
  self.energy+=.95*charge*dt/3600-discharge*dt/(.95*3600)
  net=supply+discharge-load-charge;grid_import=min(max(-net,0),c['grid_import_kw']);grid_export=min(max(net,0),c['grid_export_kw']);unserved=max(0.,-net-grid_import);curtail=max(0.,net-grid_export);actual_pv=supply-curtail
  balance=actual_pv+discharge+grid_import-(load-unserved+charge+grid_export);battery_residual=self.energy-old_energy-.95*charge*dt/3600+discharge*dt/(.95*3600)
  if abs(balance)>1e-7 or abs(battery_residual)>1e-9 or not 6-1e-8<=self.energy<=54+1e-8:raise RuntimeError('Live energy invariant failed')
  self.last={'run_id':self.run_id,'seq':self.seq,'time_s':self.t,'pv_dc_available_kw':sum(mpp),'pv_ac_kw':actual_pv,'load_kw':load,'charge_kw':charge,'discharge_kw':discharge,'grid_import_kw':grid_import,'grid_export_kw':grid_export,'curtail_kw':curtail,'unserved_kw':unserved,'soc':self.energy/60,'temperature_C':sum(self.temperature)/6,'mean_irradiance':sum(zone_g)/6,'balance_residual_kw':balance,'battery_residual_kwh':battery_residual,'battery_energy_kwh':self.energy,'irradiance_map':irr,'zone_temperature_C':self.temperature[:],'zone_dc_kw':mpp,'zone_vmpp_V':vmpp,'cloud_x':cloud_x,'config':dict(c)}
  if dt>0:self.history.append({k:self.last[k] for k in FIELDS})
  return self.last
 def step(self,dt=.1):
  if dt<=0 or dt>.1:raise ValueError('Numerical substep must be in (0,0.1] seconds')
  self.t+=dt;self.seq+=1;return self._calculate(dt)

class Runtime:
 def __init__(self,directory):
  self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True);self.lock=threading.RLock();self.model=Model();self.running=False;self.speed=1.;self.accumulator=0.;self.error=None;self.worker=None;self.stop_event=threading.Event();self.last_wall=time.monotonic();self._new_record()
 def _new_record(self):
  if hasattr(self,'file'):self.file.close()
  self.path=self.directory/(self.model.run_id+'.csv');self.file=self.path.open('w',encoding='utf-8',newline='');self.writer=csv.DictWriter(self.file,fieldnames=FIELDS);self.writer.writeheader();self.file.flush();self.events=[]
  code_files=[{'file':name,'sha256':hashlib.sha256((Path(__file__).resolve().parent/name).read_bytes()).hexdigest()} for name in ['live_simulation.py','pv_physics.py']]
  (self.directory/(self.model.run_id+'.json')).write_text(json.dumps({'schema_version':2,'created_at_utc':datetime.now(timezone.utc).isoformat(timespec='milliseconds'),'code_files':code_files,'model':MODEL,'initial_config':self.model.config,'run_id':self.model.run_id},ensure_ascii=False,indent=2),encoding='utf-8')
 def start_worker(self):
  if self.worker and self.worker.is_alive():return
  if self.file.closed:self.file=self.path.open('a',encoding='utf-8',newline='');self.writer=csv.DictWriter(self.file,fieldnames=FIELDS)
  self.stop_event.clear();self.last_wall=time.monotonic();self.worker=threading.Thread(target=self._loop,daemon=True,name='WG_Physics');self.worker.start()
 def _loop(self):
  while not self.stop_event.wait(.02):
   with self.lock:
    now=time.monotonic();elapsed=now-self.last_wall;self.last_wall=now
    if not self.running:continue
    self.accumulator+=elapsed*self.speed
    try:
     steps=0
     while self.accumulator>=.1 and steps<10:
      frame=self.model.step();self.writer.writerow({k:frame[k] for k in FIELDS});self.accumulator-=.1;steps+=1
     self.file.flush()
    except Exception as e:self.error=str(e);self.running=False
 def state(self):
  with self.lock:return dict(self.model.last)|{'running':self.running,'speed':self.speed,'backlog_sim_seconds':self.accumulator,'error':self.error,'model':MODEL}
 def command(self,action,speed=None):
  with self.lock:
   if action=='start':self.running=True;self.last_wall=time.monotonic()
   elif action=='pause':self.running=False
   elif action=='reset':self.running=False;self.model.reset(self.model.config);self.accumulator=0;self.error=None;self._new_record()
   elif action=='speed':self.speed=speed
   self._event(action,{'speed':self.speed});return self.state()
 def configure(self,config,reset=False):
  with self.lock:
   if reset:self.running=False;self.model.reset(config);self.accumulator=0;self.error=None;self._new_record()
   else:self.model.config={**DEFAULT,**config}
   self._event('reset_config' if reset else 'live_input',config);return self.state()
 def _event(self,kind,body):
  event={'seq':self.model.seq,'sim_time_s':self.model.t,'created_at_utc':datetime.now(timezone.utc).isoformat(timespec='milliseconds'),'action':kind,'body':body};self.events.append(event)
  with (self.directory/(self.model.run_id+'_events.jsonl')).open('a',encoding='utf-8') as f:f.write(json.dumps(event,ensure_ascii=False)+'\n')
 def trace(self,after=0):
  with self.lock:return [r for r in self.model.history if r['seq']>after]
 def export(self):
  with self.lock:self.file.flush();return self.path.read_text(encoding='utf-8')
 def close(self):
  self.stop_event.set()
  if self.worker:self.worker.join(timeout=5)
  with self.lock:self.file.close()
