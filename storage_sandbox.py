"""Synthetic interval-energy planning fixtures. Not a field dispatch experiment."""
import math,json,hashlib,importlib.util

def solver_status():
 available=False;versions={}
 try:
  import numpy,scipy
  from scipy.optimize import milp
  available=True;versions={'numpy':numpy.__version__,'scipy':scipy.__version__}
 except ImportError:pass
 return {'available':available,'versions':versions,'install_script':'install_dispatch_windows.bat','mode':'synthetic_fixture_only','real_experiment_ready':False}

def fixture(config):
 scenario=config['scenario'];pv=[];load=[];buy=[];sell=[]
 for h in range(24):
  # These are directly specified synthetic one-hour interval energies, not endpoint samples.
  generation=config['pv_peak_kwh']*max(0.,math.sin(math.pi*(h+.5-6)/12))/math.sin(math.pi*5.5/12)
  if scenario=='cloud_dip' and 11<=h<=14:generation*=.25
  if scenario=='low_solar':generation*=.2
  if scenario=='standby' and generation==0:generation=-.2
  if scenario=='grid_pressure':generation*=.05
  demand=(17+7*(6<=h<=8)+11*(18<=h<=21))*config['load_scale']
  pv.append(generation);load.append(demand);buy.append(.25 if h<6 or h>=23 else .95 if 17<=h<=21 else .55);sell.append(.12)
 return {'pv_kwh':pv,'load_kwh':load,'buy_price':buy,'sell_price':sell,'dt_hours':[1.]*24,'source':'手工定义的24区间合成计划，全部在决策时已知；无实测或预测误差','price_unit':'示例计价单位/kWh；非实际电价'}

def battery_parameters(c):
 return {k:c[k] for k in ['capacity_kwh','initial_soc','soc_min','soc_max','charge_kw','discharge_kw','grid_import_kw','grid_export_kw','eta_charge','eta_discharge','throughput_cost_per_kwh']}|{'terminal_soc_min':c['initial_soc']}

def constraints(result,f,b):
 n=len(f['pv_kwh']);e=result['battery_energy_kwh'];imp=result['grid_import_kwh'];exp=result['grid_export_kwh'];ch=result['charge_kwh'];dis=result['discharge_kwh'];cur=result['curtailment_kwh']
 balance=[imp[i]-exp[i]-ch[i]+dis[i]-cur[i]-(f['load_kwh'][i]-f['pv_kwh'][i]) for i in range(n)]
 transition=[e[i+1]-e[i]-b['eta_charge']*ch[i]+dis[i]/b['eta_discharge'] for i in range(n)]
 max_balance=max(map(abs,balance));max_transition=max(map(abs,transition));soc=[x/b['capacity_kwh'] for x in e]
 initial_ok=abs(soc[0]-b['initial_soc'])<1e-7
 bounds_ok=initial_ok and min(soc)>=b['soc_min']-1e-7 and max(soc)<=b['soc_max']+1e-7 and soc[-1]>=b['initial_soc']-1e-7
 rates_ok=all(imp[i]<=b['grid_import_kw']*f['dt_hours'][i]+1e-7 and exp[i]<=b['grid_export_kw']*f['dt_hours'][i]+1e-7 and ch[i]<=b['charge_kw']*f['dt_hours'][i]+1e-7 and dis[i]<=b['discharge_kw']*f['dt_hours'][i]+1e-7 and -1e-7<=cur[i]<=max(f['pv_kwh'][i],0)+1e-7 for i in range(n))
 nonnegative=all(v>=-1e-7 for values in [imp,exp,ch,dis,cur] for v in values)
 exclusive=max(ch[i]*dis[i] for i in range(n))<1e-7 and max(imp[i]*exp[i] for i in range(n))<1e-7
 return {'max_balance_residual_kwh':max_balance,'max_soc_transition_residual_kwh':max_transition,'soc_min':min(soc),'soc_max':max(soc),'terminal_soc':soc[-1],'initial_soc_matches':initial_ok,'within_soc_bounds':bounds_ok,'within_rate_bounds':rates_ok,'nonnegative_flows':nonnegative,'exclusive_modes':exclusive,'passed':max_balance<1e-6 and max_transition<1e-6 and bounds_ok and rates_ok and nonnegative and exclusive}

def baseline(f,b,greedy):
 n=len(f['pv_kwh']);e=[b['initial_soc']*b['capacity_kwh']];imp=[];exp=[];charge=[];discharge=[];curtail=[];floor=max(b['soc_min'],b['initial_soc'])*b['capacity_kwh']
 for i in range(n):
  net=f['pv_kwh'][i]-f['load_kwh'][i];ch=dis=0.
  if greedy and net>0:ch=max(0.,min(net,b['charge_kw'],(b['soc_max']*b['capacity_kwh']-e[-1])/b['eta_charge']))
  elif greedy and net<0:dis=max(0.,min(-net,b['discharge_kw'],(e[-1]-floor)*b['eta_discharge']))
  net=net-ch+dis;buy=max(0.,-net);sold=min(max(0.,net),b['grid_export_kw']);cut=max(0.,net-sold)
  if buy>b['grid_import_kw']+1e-7:return {'status':'infeasible','reason':'进口上限不能满足该规则下的负荷；未隐式丢弃负荷'}
  imp.append(buy);exp.append(sold);charge.append(ch);discharge.append(dis);curtail.append(cut);e.append(e[-1]+b['eta_charge']*ch-dis/b['eta_discharge'])
 objective=sum(imp[i]*f['buy_price'][i]-exp[i]*f['sell_price'][i]+b['throughput_cost_per_kwh']*(charge[i]+discharge[i]) for i in range(n))
 return {'status':'optimal' if not greedy else 'rule_feasible','grid_import_kwh':imp,'grid_export_kwh':exp,'charge_kwh':charge,'discharge_kwh':discharge,'curtailment_kwh':curtail,'battery_energy_kwh':e,'objective_currency':objective}

def run(config):
 f=fixture(config);b=battery_parameters(config);strategies={}
 for key,greedy in [('no_storage',False),('reserve_rule',True)]:strategies[key]=baseline(f,b,greedy)
 try:
  from dispatch_milp import solve_energy_dispatch
 except ImportError as exc:raise RuntimeError('优化依赖尚未安装。双击install_dispatch_windows.bat，安装后重启平台；其他页面可继续使用。') from exc
 try:
  raw=solve_energy_dispatch(f['pv_kwh'],f['load_kwh'],f['buy_price'],f['sell_price'],f['dt_hours'],**b)
  strategies['milp']={k:v.tolist() if hasattr(v,'tolist') else v for k,v in raw.items()};strategies['milp']['status']='optimal'
 except ValueError as e:strategies['milp']={'status':'infeasible_or_unsolved','reason':str(e)}
 for result in strategies.values():
  if 'battery_energy_kwh' not in result:continue
  result['validation']=constraints(result,f,b)
  if not result['validation']['passed']:raise ValueError('调度结果未通过独立能量/SOC/边界检查')
  result['soc']=[v/b['capacity_kwh'] for v in result['battery_energy_kwh']]
  result['grid_import_total_kwh']=sum(result['grid_import_kwh']);result['curtailment_total_kwh']=sum(result['curtailment_kwh']);result['battery_throughput_kwh']=sum(result['charge_kwh'])+sum(result['discharge_kwh'])
 baseline_cost=strategies['no_storage'].get('objective_currency');optimized_cost=strategies['milp'].get('objective_currency')
 return {'solver':solver_status()|{'core':'V29 dispatch_milp.py / SciPy-HiGHS MILP'},'mode':'synthetic_fixture_only','config':config,'config_sha256':hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest(),'fixture':f,'battery':b,'strategies':strategies,'fixture_cost_difference':baseline_cost-optimized_cost if baseline_cost is not None and optimized_cost is not None else None,'scope':'24区间已知计划的确定性优化，非预测闭环、滚动MPC或真实收益实验','fairness':'所有策略从同一SOC开始，终端SOC至少恢复初值；规则策略始终保留初始SOC作为保守储备。','real_data_gate':'BLOCKED_FOR_REAL_COST_EXPERIMENT','limits':['不使用V28/V30未认证单位的输出充当kWh','合成负荷为外生已知计划，无负荷预测模型','线性吞吐成本为示例，不是电池寿命标定','无执行扰动、预测误差、备用机组或隐式负荷削减']}
