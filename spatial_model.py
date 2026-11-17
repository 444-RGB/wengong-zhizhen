"""Synthetic geometric scene, first-hit ray scanning and geometric beam occlusion.
Units metres, Y up internally. Known poses, NOT SLAM or real survey data.
"""
import math,random,hashlib,json,io
from dataclasses import dataclass
from functools import lru_cache

def dot(a,b):return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]
def add(a,b):return tuple(x+y for x,y in zip(a,b))
def sub(a,b):return tuple(x-y for x,y in zip(a,b))
def mul(a,s):return tuple(x*s for x in a)
def norm(a):return math.sqrt(dot(a,a))
def cross(a,b):return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
@dataclass
class Face:
 center:tuple;u:tuple;v:tuple;hu:float;hv:float;object_id:int
 def __post_init__(self):
  self.normal=cross(self.u,self.v);corners=[add(self.center,add(mul(self.u,s*self.hu),mul(self.v,t*self.hv))) for s in [-1,1] for t in [-1,1]]
  self.low=tuple(min(p[i] for p in corners)-1e-6 for i in range(3));self.high=tuple(max(p[i] for p in corners)+1e-6 for i in range(3))
 def hit(self,o,d,limit):
  den=dot(self.normal,d)
  if abs(den)<1e-12:return None
  t=dot(self.normal,sub(self.center,o))/den
  if t<=1e-5 or t>=limit:return None
  p=sub(add(o,mul(d,t)),self.center)
  return t if abs(dot(p,self.u))<=self.hu+1e-9 and abs(dot(p,self.v))<=self.hv+1e-9 else None

def box(center,size,object_id):
 faces=[];axes=[(1,0,0),(0,1,0),(0,0,1)]
 for i in range(3):
  other=[j for j in range(3) if j!=i]
  for sign in [-1,1]:faces.append(Face(add(center,mul(axes[i],sign*size[i]/2)),axes[other[0]],axes[other[1]],size[other[0]]/2,size[other[1]]/2,object_id))
 return faces
class Tree:
 def __init__(self,faces):
  self.low=tuple(min(f.low[i] for f in faces) for i in range(3));self.high=tuple(max(f.high[i] for f in faces) for i in range(3));self.children=None
  if len(faces)<=6:self.faces=faces
  else:
   axis=max(range(3),key=lambda i:self.high[i]-self.low[i]);ordered=sorted(faces,key=lambda f:f.center[axis]);mid=len(faces)//2;self.faces=[];self.children=(Tree(ordered[:mid]),Tree(ordered[mid:]))
 def intersects(self,o,d,limit):
  near=0.;far=limit
  for i in range(3):
   if abs(d[i])<1e-14:
    if o[i]<self.low[i] or o[i]>self.high[i]:return False
   else:
    a=(self.low[i]-o[i])/d[i];b=(self.high[i]-o[i])/d[i]
    near=max(near,min(a,b));far=min(far,max(a,b))
    if near>far:return False
  return True
 def cast(self,o,d,limit=200.,ignore=-1):
  if not self.intersects(o,d,limit):return None
  best=None
  if self.children:
   for child in self.children:
    hit=child.cast(o,d,limit,ignore)
    if hit is not None:best=hit;limit=hit[0]
  else:
   for f in self.faces:
    if f.object_id==ignore:continue
    t=f.hit(o,d,limit)
    if t is not None:best=(t,f);limit=t
  return best

@lru_cache(maxsize=8)
def scene(obstacle_height=0.):
 faces=[Face((0,0,0),(1,0,0),(0,0,1),95,85,0)];panels=[];c=math.cos(math.pi/6);s=.5
 for si in range(6):
  zx=-38+(si%3)*19;zz=-24+(si//3)*20
  for rr in range(2):
   gx=zx+5.8;gz=zz+rr*7
   for j in range(2):
    for k in range(5):
     mi=rr*10+j*5+k;z=(j-.5)*2.19;face=Face((gx+(k-2)*1.29,1.9+.065*c+s*z,gz-.065*s+c*z),(1,0,0),(0,s,c),1.22/2,2.12/2,1+si*20+mi);faces.append(face);panels.append(face)
   for x in [-2.4,2.4]:
    for off in [-1.4,1.4]:
     h=1.9+off*.5;faces+=box((gx+x,h/2+.3,gz+off),(.1,h,.1),500+si)
 faces+=box((38,2.4,-17),(4.8,3.9,2.5),201);faces+=box((38,2.8,5),(4.7,4.5,3),202);faces+=box((38,2.3,29),(8,3.7,6),203)
 for a,b in [((-47,-33),(47,-33)),((47,-33),(47,34)),((47,34),(-47,34)),((-47,34),(-47,-33))]:
  count=math.ceil(math.dist(a,b)/3)
  for j in range(count+1):
   u=j/count;x=a[0]+(b[0]-a[0])*u;z=a[1]+(b[1]-a[1])*u;faces+=box((x,.9,z),(.08,1.8,.08),400)
 if obstacle_height>0:faces+=box((-13,obstacle_height/2,-33),(28,obstacle_height,2),301)
 return Tree(faces),panels

def scan(config):
 tree,panels=scene(config['obstacle_height']);rng=random.Random(config['seed']);poses=[(-42,8,19),(23,8,-31),(22,8,18),(-42,8,-31)][:config['stations']];points=[];hits=0;counts={};rays=config['azimuth_samples']*config['vertical_samples']*len(poses)
 for station,o in enumerate(poses):
  for va in range(config['vertical_samples']):
   elevation=math.radians(-42+50*va/(config['vertical_samples']-1));ce=math.cos(elevation);se=math.sin(elevation)
   for az in range(config['azimuth_samples']):
    angle=2*math.pi*az/config['azimuth_samples'];d=(ce*math.sin(angle),se,ce*math.cos(angle));hit=tree.cast(o,d,config['max_range'])
    if hit is None:continue
    hits+=1
    if rng.random()<config['dropout']:continue
    distance,f=hit;distance=max(.01,distance+rng.gauss(0,config['range_noise']));p=add(o,mul(d,distance));points.append([round(v,5) for v in p]+[f.object_id,station,round(distance,5)]);counts[f.object_id]=counts.get(f.object_id,0)+1
 return {'mode':'synthetic_first_return_known_pose','config':config,'config_sha256':hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest(),'axis':'Y-up metres; export uses Z-up (x,-z,y)','poses':poses,'rays':rays,'geometry_hits':hits,'points':points,'point_count':len(points),'observed_panel_count':len([k for k in counts if 1<=k<=120]),'label_source':'模拟器几何真值ID，不是识别算法预测','limits':['已知位姿直接合并，无SLAM、配准或位姿估计','高斯测距噪声与独立丢点为理想化假设，非传感器标定','简化不透明矩形表面，无玻璃多径、入射角反射与运动畸变'],'object_counts':counts}

def shading(config):
 tree,panels=scene(config['obstacle_height']);az=math.radians(config['sun_azimuth']);el=math.radians(config['sun_elevation']);sun=(math.sin(az)*math.cos(el),math.sin(el),-math.cos(az)*math.cos(el))
 cosinc=max(0,dot((0,math.cos(math.pi/6),-.5),sun))
 direct=config['dni']*cosinc;base=config['diffuse_poa']+direct;fractions=[]
 n=config.get('samples',3)
 for face in panels:
  blocked=0
  for j in range(n):
   for k in range(n):
    p=add(face.center,add(mul(face.u,((j+.5)/n*2-1)*face.hu),mul(face.v,((k+.5)/n*2-1)*face.hv)));origin=add(p,mul(sun,1e-4))
    if tree.cast(origin,sun,200.,face.object_id) is not None:blocked+=1
  fractions.append(blocked/(n*n))
 effective=[config['diffuse_poa']+direct*(1-f) for f in fractions];factors=[x/base if base>0 else 1. for x in effective]
 return {'mode':'geometry_beam_occlusion','config':config,'direct_poa_Wm2':direct,'unshaded_poa_Wm2':base,'beam_blocked_fraction':[fractions[i:i+20] for i in range(0,120,20)],'effective_irradiance':[effective[i:i+20] for i in range(0,120,20)],'physics_config':{'irradiance':base,'temperature':config['temperature'],'transmission':1.,'shade':[factors[i:i+20] for i in range(0,120,20)]},'limits':['仅对直接辐照做几何遮挡；散射POA固定，无天空视域与反射计算','太阳角为用户指定，非经纬度/日期太阳位置求解','几何为内置简化场站，不从用户载入的GLB或点云反演','默认每组件3×3采样；采样细化用于检查数值敏感性，非现场精度保证']}

def pcd_text(result):
 points=result['points'];header='# .PCD v0.7 - Synthetic known-pose ray scan; Z-up metres\nVERSION 0.7\nFIELDS x y z object_id\nSIZE 4 4 4 4\nTYPE F F F I\nCOUNT 1 1 1 1\nWIDTH '+str(len(points))+'\nHEIGHT 1\nVIEWPOINT 0 0 0 1 0 0 0\nPOINTS '+str(len(points))+'\nDATA ascii\n'
 return header+''.join(f'{p[0]:.5f} {-p[2]:.5f} {p[1]:.5f} {p[3]}\n' for p in points)
