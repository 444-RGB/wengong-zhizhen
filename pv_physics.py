"""Quasi-static synthetic DC-array solver, not a calibrated plant model.
Single diode + De Soto auxiliaries + constant-drop bypass approximation.
Each module is uniformly illuminated internally; no cell hot-spot model.
"""
import math,bisect,hashlib,json
from collections import Counter
from functools import lru_cache
REFERENCE={'IL_ref_A':13.0,'I0_ref_A':1e-10,'a_ref_V':1.85,'Rs_ohm':.25,'Rsh_ref_ohm':400.,'alpha_sc_A_K':.0065,'Eg_ref_eV':1.121,'dEg_dT_per_K':-.0002677,'reference_temperature_C':25.,'reference_irradiance_W_m2':1000.,'bypass_drop_per_module_V':1.5,'series_modules':20,'parallel_strings':6}

def parameters(irradiance,temperature):
 r=REFERENCE;t=temperature+273.15;t0=298.15;eg=r['Eg_ref_eV']*(1+r['dEg_dT_per_K']*(t-t0));kb=8.617332478e-5
 il=irradiance/1000*(r['IL_ref_A']+r['alpha_sc_A_K']*(t-t0))
 i0=r['I0_ref_A']*(t/t0)**3*math.exp(r['Eg_ref_eV']/(kb*t0)-eg/(kb*t))
 return il,i0,r['Rs_ohm'],r['Rsh_ref_ohm']*1000/max(irradiance,1e-9),r['a_ref_V']*t/t0

def voltage_at_current(current,params):
 il,i0,rs,rsh,a=params;drop=REFERENCE['bypass_drop_per_module_V'];lo=-drop+current*rs;hi=a*math.log1p(il/i0)
 def residual(vd):return il-i0*math.expm1(min(700,vd/a))-vd/rsh-current
 if residual(lo)<=0:return -drop
 for _ in range(38):
  mid=(lo+hi)/2
  if residual(mid)>0:lo=mid
  else:hi=mid
 return (lo+hi)/2-current*rs

@lru_cache(maxsize=512)
def module_curve(irradiance,temperature,resolution=1600):
 params=parameters(irradiance,temperature)
 if irradiance<=0:return (0.,),(0.,),0.,0.
 imax=params[0]*1.015;currents=tuple(imax*j/resolution for j in range(resolution+1));voltages=tuple(voltage_at_current(i,params) for i in currents)
 m=max((max(0,v)*i,v) for v,i in zip(voltages,currents));return currents,voltages,m[0],m[1]

def string_curve(gs,temperature,resolution=1600):
 groups=Counter(round(g,6) for g in gs);imax=max(parameters(g,temperature)[0] for g in groups)*1.015
 if imax<=0:return [0.,0.],[0.,0.],0.,0.
 # All groups share the same current grid; solve module voltages then add in series.
 currents=[imax*j/resolution for j in range(resolution+1)]
 voltages=[0.]*(resolution+1)
 for g,count in groups.items():
  par=parameters(g,temperature)
  for j,i in enumerate(currents):voltages[j]+=count*voltage_at_current(i,par)
 peak=max((max(0,v)*i,v) for v,i in zip(voltages,currents))
 return currents,voltages,peak[0],peak[1]

def interpolate(x,xs,ys):
 j=bisect.bisect_right(xs,x)
 if j==0:return ys[0]
 if j>=len(xs):return ys[-1]
 a,b=xs[j-1],xs[j]
 return ys[j-1]+(ys[j]-ys[j-1])*(x-a)/(b-a) if b>a else ys[j]

def solve(config,resolution=1600,detail=True):
 g=config['irradiance'];temp=config['temperature'];tau=config['transmission'];shade=config['shade']
 effective=[[round(g*tau*x,6) for x in string] for string in shade]
 curves=[string_curve(s,temp,resolution) for s in effective]
 voc=max(c[1][0] for c in curves);volts=[voc*j/1200 for j in range(1201)] if voc>0 else [0.]*1201
 reverse=[([-v for v in c[1]],c[0]) for c in curves]
 current_rows=[[max(0.,interpolate(-v,xs,ys)) if v<curve[1][0] else 0. for v in volts] for (xs,ys),curve in zip(reverse,curves)]
 amps=[sum(row[j] for row in current_rows) for j in range(len(volts))];power=[v*i for v,i in zip(volts,amps)];idx=max(range(len(power)),key=power.__getitem__);pmp=power[idx];vmp=volts[idx]
 isolated=sum(module_curve(x,temp,resolution)[2] for s in effective for x in s);clear=120*module_curve(g,temp,resolution)[2];uniform_g=sum(sum(s) for s in effective)/120;uniform=120*module_curve(round(uniform_g,6),temp,resolution)[2]
 peaks=[j for j in range(1,len(power)-1) if power[j]>power[j-1] and power[j]>=power[j+1] and power[j]>.001]
 bypass=[]
 for eff,row in zip(effective,current_rows):
  i=row[idx];bypass.append([voltage_at_current(i,parameters(x,temp))<=-1.5+1e-6 for x in eff])
 out={'model':'SDE-DeSoto-constant-bypass-v1','config':config,'config_sha256':hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest(),'units':{'voltage':'V','current':'A','power':'W','irradiance':'W/m2','temperature':'cell degC'},'effective_irradiance':effective,'pmp_W':pmp,'vmp_V':vmp,'imp_A':amps[idx],'voc_V':voc,'isc_A':amps[0],'reference_clear_W':clear,'isolated_modules_upper_W':isolated,'independent_strings_upper_W':sum(c[2] for c in curves),'uniform_same_mean_W':uniform,'mismatch_loss_W':max(0,isolated-pmp),'bypass_modules':bypass,'local_peaks':[{'voltage_V':volts[j],'power_W':power[j]} for j in peaks],'string_mpp_W':[c[2] for c in curves],'string_operating_A':[r[idx] for r in current_rows]}
 if detail:out['curve']=[{'voltage_V':v,'current_A':i,'power_W':p} for v,i,p in zip(volts,amps,power)]
 return out

def search_mppt(result,start_voltage,step,iterations=100):
 c=result['curve'];xs=[r['voltage_V'] for r in c];ys=[r['power_W'] for r in c];v=min(xs[-1],max(0.,start_voltage));direction=1;history=[];previous=None
 # Ideal, settled plant at every perturbation; iteration is not physical time.
 for k in range(iterations):
  p=interpolate(v,xs,ys);history.append({'iteration':k,'voltage_V':v,'power_W':p})
  if previous is not None and p<previous:direction=-direction
  previous=p;v=max(0.,min(xs[-1],v+direction*step))
  if v==0 or v==xs[-1]:direction=-direction
 tail=history[-10:];mean=sum(r['power_W'] for r in tail)/len(tail)
 return {'method':'ideal_static_P&O','trajectory':history,'last10_mean_W':mean,'sampled_global_mpp_W':result['pmp_W'],'last10_to_global_ratio':mean/result['pmp_W'] if result['pmp_W'] else None,'clock':'iteration; no electrical transient or physical tracking-time claim'}
