"""Physical energy-domain PV–BESS MILP. Solves supplied verified scenarios only.
No load forecast model, automatic point-to-energy conversion, or real-data defaults.
"""
import numpy as np
from scipy.optimize import milp,Bounds,LinearConstraint

def solve_energy_dispatch(pv_kwh,load_kwh,buy_price,sell_price,dt_hours,*,capacity_kwh,initial_soc,soc_min,soc_max,charge_kw,discharge_kw,grid_import_kw,grid_export_kw,eta_charge,eta_discharge,throughput_cost_per_kwh,terminal_soc_min=None):
 pv=np.asarray(pv_kwh,float);load=np.asarray(load_kwh,float);buy=np.asarray(buy_price,float);sell=np.asarray(sell_price,float);dt=np.asarray(dt_hours,float)
 n=len(pv);assert n>0 and all(v.shape==(n,) and np.isfinite(v).all() for v in [pv,load,buy,sell,dt])
 assert np.all(load>=0) and np.all(dt>0) and capacity_kwh>0 and 0<=soc_min<=initial_soc<=soc_max<=1
 assert 0<eta_charge<=1 and 0<eta_discharge<=1 and min(charge_kw,discharge_kw,grid_import_kw,grid_export_kw,throughput_cost_per_kwh)>=0
 if terminal_soc_min is not None:assert soc_min<=terminal_soc_min<=soc_max
 # Signed PV is not discarded: a negative PV value increases net demand.
 size=8*n+1;B=0;S=n;C=2*n;D=3*n;K=4*n;E=5*n;ZB=6*n+1;ZG=7*n+1
 c=np.zeros(size);c[B:B+n]=buy;c[S:S+n]=-sell;c[C:C+n]=throughput_cost_per_kwh;c[D:D+n]=throughput_cost_per_kwh
 lb=np.zeros(size);ub=np.full(size,np.inf)
 for start,rate in [(B,grid_import_kw),(S,grid_export_kw),(C,charge_kw),(D,discharge_kw)]:ub[start:start+n]=rate*dt
 ub[K:K+n]=np.maximum(pv,0);lb[E:E+n+1]=soc_min*capacity_kwh;ub[E:E+n+1]=soc_max*capacity_kwh;lb[E]=ub[E]=initial_soc*capacity_kwh
 if terminal_soc_min is not None:lb[E+n]=max(lb[E+n],terminal_soc_min*capacity_kwh)
 ub[ZB:ZG+n]=1;integrality=np.zeros(size,int);integrality[ZB:ZG+n]=1
 rows=[];lower=[];upper=[]
 def constraint(items,lo,hi):
  row=np.zeros(size)
  for j,v in items:row[j]=v
  rows.append(row);lower.append(lo);upper.append(hi)
 for i in range(n):
  constraint([(B+i,1),(S+i,-1),(C+i,-1),(D+i,1),(K+i,-1)],load[i]-pv[i],load[i]-pv[i])
  constraint([(E+i+1,1),(E+i,-1),(C+i,-eta_charge),(D+i,1/eta_discharge)],0,0)
  constraint([(C+i,1),(ZB+i,-charge_kw*dt[i])],-np.inf,0)
  constraint([(D+i,1),(ZB+i,discharge_kw*dt[i])],-np.inf,discharge_kw*dt[i])
  constraint([(B+i,1),(ZG+i,-grid_import_kw*dt[i])],-np.inf,0)
  constraint([(S+i,1),(ZG+i,grid_export_kw*dt[i])],-np.inf,grid_export_kw*dt[i])
 result=milp(c,integrality=integrality,bounds=Bounds(lb,ub),constraints=LinearConstraint(np.stack(rows),np.asarray(lower),np.asarray(upper)),options={'mip_rel_gap':1e-9})
 if not result.success:raise ValueError('Dispatch infeasible or unsolved: '+result.message)
 z=result.x;out={'grid_import_kwh':z[B:B+n],'grid_export_kwh':z[S:S+n],'charge_kwh':z[C:C+n],'discharge_kwh':z[D:D+n],'curtailment_kwh':z[K:K+n],'battery_energy_kwh':z[E:E+n+1],'objective_currency':float(result.fun)}
 a=out;balance=a['grid_import_kwh']-a['grid_export_kwh']-a['charge_kwh']+a['discharge_kwh']-a['curtailment_kwh']-(load-pv)
 transition=np.diff(a['battery_energy_kwh'])-eta_charge*a['charge_kwh']+a['discharge_kwh']/eta_discharge
 assert np.max(np.abs(balance))<1e-6 and np.max(np.abs(transition))<1e-6
 assert np.max(a['charge_kwh']*a['discharge_kwh'])<1e-7 and np.max(a['grid_import_kwh']*a['grid_export_kwh'])<1e-7
 return out

def check_real_data_contract(metadata,forecast_table):
 """Fail closed until unit/clock/provenance and interval target are certified."""
 required=['physical_units_verified','clock_mapping_verified','same_site_load_tariff_verified','battery_parameters_verified']
 missing=[k for k in required if metadata.get(k) is not True]
 if metadata.get('forecast_target')!='interval_energy_kwh':missing.append('forecast_target=interval_energy_kwh')
 if missing:raise ValueError('Real-data dispatch blocked: '+', '.join(missing))
 required_cols=['issued_at','decision_at','valid_start','valid_end','forecast_energy_kwh','site_id']
 if not all(c in forecast_table for c in required_cols):raise ValueError('Issued interval forecast columns missing')
 import pandas as pd
 
 for column in ['issued_at','decision_at','valid_start','valid_end']:
  if any(pd.Timestamp(v).tzinfo is None for v in forecast_table[column]):raise ValueError('Explicit timezone offset required; no guessed UTC')
 issued=pd.to_datetime(forecast_table.issued_at,utc=True);decision=pd.to_datetime(forecast_table.decision_at,utc=True);start=pd.to_datetime(forecast_table.valid_start,utc=True);end=pd.to_datetime(forecast_table.valid_end,utc=True)
 if (issued>decision).any() or (start<decision).any() or (end<=start).any():raise ValueError('Forecast timing violates availability contract')
 if len(set(forecast_table.site_id))!=1 or list(forecast_table.site_id)[0]!=metadata.get('site_id'):raise ValueError('Site mapping mismatch')
 return True

def solve_verified_schedule(metadata,forecast_table,load_and_price_schedule,battery_parameters):
 """Public real-data entry. No measured future load accepted as a decision input."""
 check_real_data_contract(metadata,forecast_table)
 import pandas as pd
 exo=load_and_price_schedule.reset_index(drop=True);f=forecast_table.reset_index(drop=True)
 required=['valid_start','valid_end','load_schedule_issued_at','tariff_issued_at','load_energy_kwh','buy_price_per_kwh','sell_price_per_kwh','site_id','load_input_kind']
 if not all(k in exo for k in required) or len(exo)!=len(f):raise ValueError('Aligned issued load/price schedule required')
 if not exo.load_input_kind.eq('known_or_externally_issued_schedule').all():raise ValueError('Unissued future measured load/oracle input is not a deployable load schedule')
 for k in ['valid_start','valid_end','load_schedule_issued_at','tariff_issued_at']:
  if any(pd.Timestamp(v).tzinfo is None for v in exo[k]):raise ValueError('Explicit exogenous clock offset required')
 decision=pd.to_datetime(f.decision_at,utc=True)
 if len(set(decision))!=1:raise ValueError('One issue decision per optimization call')
 start=pd.to_datetime(f.valid_start,utc=True);end=pd.to_datetime(f.valid_end,utc=True)
 if not start.equals(pd.to_datetime(exo.valid_start,utc=True)) or not end.equals(pd.to_datetime(exo.valid_end,utc=True)):raise ValueError('Forecast and exogenous interval mismatch')
 if (pd.to_datetime(exo.load_schedule_issued_at,utc=True)>decision).any() or (pd.to_datetime(exo.tariff_issued_at,utc=True)>decision).any():raise ValueError('Exogenous schedule issued after decision')
 if not exo.site_id.eq(metadata['site_id']).all():raise ValueError('Exogenous site mismatch')
 if not start.is_monotonic_increasing or start.duplicated().any() or (len(start)>1 and not np.array_equal(start.to_numpy()[1:],end.to_numpy()[:-1])):raise ValueError('Optimization intervals must be ordered and contiguous')
 if start.iloc[0]!=decision.iloc[0]:raise ValueError('First optimization interval must start at decision time')
 dt=(end-start).dt.total_seconds().to_numpy()/3600
 return solve_energy_dispatch(f.forecast_energy_kwh.to_numpy(),exo.load_energy_kwh.to_numpy(),exo.buy_price_per_kwh.to_numpy(),exo.sell_price_per_kwh.to_numpy(),dt,**battery_parameters)
