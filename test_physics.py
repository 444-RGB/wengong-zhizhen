"""Physics invariants, convergence and API contracts; stdlib + existing dev dependencies."""
import math,time,os,tempfile,sys
TEST_RUNTIME=tempfile.TemporaryDirectory();os.environ['WG_RUNTIME_DIR']=TEST_RUNTIME.name
from pv_physics import parameters,voltage_at_current,solve,search_mppt,module_curve

def test_physics():
 for g in [100,600,1000,1300]:
  for t in [-20,25,85]:
   par=parameters(g,t);il,i0,rs,rsh,a=par
   for ratio in [0,.2,.7,.94]:
    i=il*ratio;v=voltage_at_current(i,par);vd=v+i*rs
    residual=il-i0*math.expm1(vd/a)-vd/rsh-i
    assert abs(residual)<1e-7,(g,t,ratio,residual)
 # Independent closed-form check with Rs=0 and infinite shunt resistance.
 ideal=(13.,1e-10,0.,math.inf,1.85)
 for i in [0,2,8,12]:
  expected=1.85*math.log1p((13-i)/1e-10)
  assert abs(voltage_at_current(i,ideal)-expected)<1e-8
 clear={'irradiance':1000.,'temperature':25.,'transmission':1.,'shade':[[1.]*20 for _ in range(6)]}
 start=time.monotonic();r=solve(clear)
 assert abs(r['pmp_W']-120*module_curve(1000,25)[2])/r['pmp_W']<1e-4
 assert abs(r['pmp_W']-r['isolated_modules_upper_W'])<1
 assert r['mismatch_loss_W']<1
 assert all(a['current_A']>=b['current_A']-1e-8 for a,b in zip(r['curve'],r['curve'][1:]))
 hot=solve({**clear,'temperature':65.});dim=solve({**clear,'irradiance':500.})
 assert hot['pmp_W']<r['pmp_W'] and .45<dim['pmp_W']/r['pmp_W']<.55
 shaded={**clear,'shade':[[.2]*7+[1.]*13 for _ in range(6)]};s=solve(shaded)
 assert len(s['local_peaks'])>=2 and sum(sum(x) for x in s['bypass_modules'])==42
 assert s['pmp_W']<=s['independent_strings_upper_W']+2<=s['isolated_modules_upper_W']+4
 near=search_mppt(s,480,5);far=search_mppt(s,800,5)
 assert near['last10_to_global_ratio']>.98 and far['last10_to_global_ratio']<.5
 fine=solve(shaded,resolution=3200)
 assert abs(fine['pmp_W']-s['pmp_W'])/fine['pmp_W']<.001
 zero=solve({**clear,'irradiance':0.});assert zero['pmp_W']==0 and zero['isc_A']==0
 from fastapi.testclient import TestClient
 from server import app
 with TestClient(app) as c:
  assert c.get('/api/physics/info').status_code==200
  result=c.post('/api/physics/solve',json=shaded);assert result.status_code==200
  assert len(result.json()['curve'])==1201
  assert c.post('/api/physics/solve',json={**clear,'temperature':99}).status_code==422
  assert c.post('/api/physics/solve',json={**clear,'shade':[[1.]*20]}).status_code==400
  assert c.post('/api/physics/search',json={**shaded,'start_voltage':800,'step':5}).json()['last10_to_global_ratio']<.5
  scan=c.post('/api/physics/scan',json=clear).json();assert len(scan['points'])==14
  assert c.post('/api/physics/export',json=shaded).text.lstrip('\ufeff').startswith('voltage_V,current_A,power_W')
 print('PASS: SDE KCL, analytic ideal case, array scaling, weak light/high temperature, multiple peaks, bypass count, local-search dependence, grid convergence, darkness, API validation/scan/export. Seconds:',round(time.monotonic()-start,2))

if __name__=='__main__':test_physics()
