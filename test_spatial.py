import math,random,os,tempfile,time
os.environ['WG_RUNTIME_DIR']=tempfile.mkdtemp(prefix='wg_spatial_test_')
from spatial_model import Face,Tree,scene,scan,shading,pcd_text,add,mul

def test_spatial():
 near=Face((0,2,5),(1,0,0),(0,1,0),2,2,1);far=Face((0,2,10),(1,0,0),(0,1,0),2,2,2);tree=Tree([far,near])
 assert tree.cast((0,2,0),(0,0,1))[0]==5
 assert tree.cast((0,2,0),(0,0,1),ignore=1)[0]==10
 assert tree.cast((5,2,0),(0,0,1)) is None
 # Compare accelerated traversal to an exhaustive, independent face enumeration.
 tree,_=scene(6);all_faces=[]
 def collect(t):
  all_faces.extend(t.faces)
  if t.children:
   for c in t.children:collect(c)
 collect(tree);rng=random.Random(9)
 for _ in range(150):
  o=(rng.uniform(-55,55),rng.uniform(2,15),rng.uniform(-45,45));d=(rng.uniform(-1,1),rng.uniform(-1,1),rng.uniform(-1,1));norm=math.sqrt(sum(x*x for x in d));d=tuple(x/norm for x in d);hit=tree.cast(o,d,100);expected=[]
  for f in all_faces:
   dist=f.hit(o,d,100)
   if dist is not None:expected.append(dist)
  assert (hit is None)==(not expected)
  if hit:assert abs(hit[0]-min(expected))<1e-8
 config={'obstacle_height':0.,'stations':2,'azimuth_samples':180,'vertical_samples':16,'max_range':100.,'range_noise':0.,'dropout':0.,'seed':42}
 clean=scan(config);assert clean['point_count']>1000 and clean['points']==scan(config)['points'];assert clean['geometry_hits']==clean['point_count']
 noisy=scan({**config,'range_noise':.02});assert len(noisy['points'])==len(clean['points'])
 rmse=math.sqrt(sum((a[5]-b[5])**2 for a,b in zip(clean['points'],noisy['points']))/len(clean['points']));assert .018<rmse<.022
 dropped=scan({**config,'dropout':.3});assert .65<dropped['point_count']/dropped['geometry_hits']<.75
 text=pcd_text(clean);lines=text.split('DATA ascii\n')[1].splitlines();assert len(lines)==clean['point_count'];p=clean['points'][0];q=lines[0].split();assert [float(x) for x in q[:3]]==[p[0],-p[2],p[1]]
 env={'sun_azimuth':0.,'sun_elevation':5.,'dni':800.,'diffuse_poa':100.,'temperature':25.,'obstacle_height':0.,'samples':3}
 low=shading(env);high=shading({**env,'sun_elevation':40.});assert sum(sum(r) for r in low['beam_blocked_fraction'])>sum(sum(r) for r in high['beam_blocked_fraction'])
 assert all(100-1e-9<=v<=low['unshaded_poa_Wm2']+1e-9 for row in low['effective_irradiance'] for v in row)
 night=shading({**env,'dni':0.,'obstacle_height':10.});assert all(x==1 for row in night['physics_config']['shade'] for x in row)
 from fastapi.testclient import TestClient
 import server
 with TestClient(server.app) as c:
  r=c.post('/api/spatial/scan',json=config);assert r.status_code==200 and r.json()['point_count']==clean['point_count']
  assert c.post('/api/spatial/scan',json={**config,'stations':5}).status_code==422
  assert c.post('/api/spatial/export',json=config).text==text
  s=c.post('/api/spatial/shadow',json=env).json();assert s['occlusion_power_difference_W']>0 and s['dc_summary']['pmp_W']>0
  s2=c.post('/api/physics/solve',json=s['physics_config']).json();assert s['dc_summary']['pmp_W']==s2['pmp_W']
  assert c.post('/api/spatial/shadow',json={**env,'sun_elevation':-5}).status_code==422
 print('PASS: nearest hit and occlusion, BVH vs exhaustive 150 rays, deterministic scan/noise/dropout, PCD axis/count, geometric shading bounds, geometric-to-DC consistency and API validation.')
if __name__=='__main__':test_spatial()
