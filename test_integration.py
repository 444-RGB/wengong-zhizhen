"""Offline import and Blender asset integrity. MATLAB is NOT run by this test."""
import csv,io,json,hashlib,os,tempfile,zipfile,struct,copy
from pathlib import Path
TMP=tempfile.TemporaryDirectory();os.environ['WG_RUNTIME_DIR']=TMP.name
from fastapi.testclient import TestClient
import server
from simulink_bridge import validate,Bridge
ROOT=Path(__file__).resolve().parent

def run():
 manifest=json.loads((ROOT/'static/integration/archive_source.json').read_text());text=(ROOT/'static/integration/archive_signals.csv').read_bytes().decode('utf-8')
 rows,summary=validate(text,manifest);assert len(rows)==manifest['sample_count']
 with TestClient(server.app) as c:
  live=c.get('/api/live/state').json();archive=c.get('/api/state').json()
  assert c.get('/integration').status_code==200
  info=c.get('/api/integration/info').json();assert not info['matlab_online']
  raw=c.get(info['model']['glb_url']).content;assert hashlib.sha256(raw).hexdigest()==info['model']['glb_sha256']
  magic,version,size=struct.unpack_from('<III',raw);assert (magic,version,size)==(0x46546c67,2,len(raw))
  length,kind=struct.unpack_from('<II',raw,12);gltf=json.loads(raw[20:20+length]);panels=[n for n in gltf['nodes'] if n.get('name','').startswith('PV_S')];assert len(panels)==120 and len({n['name'] for n in panels})==120 and all('mesh' in n for n in panels)
  assert not any('uri' in x for x in gltf.get('buffers',[]));assert 'KHR_draco_mesh_compression' not in gltf.get('extensionsRequired',[])
  blend=c.get('/api/integration/blend').content;assert blend.startswith(b'BLENDER') and hashlib.sha256(blend).hexdigest()==info['model']['blend_sha256']
  with zipfile.ZipFile(io.BytesIO(c.get('/api/integration/exporter').content)) as z:assert set(z.namelist())=={'wg_export_simulink.m','wg_export_mapping_example.m'}
  body={'title':'离线归档导入验证','csv_text':text,'manifest':manifest};r=c.post('/api/integration/preview',json=body);assert r.status_code==200,r.text
  assert c.get('/api/integration/records').json()==[]
  r=c.post('/api/integration/import',json=body);assert r.status_code==200,r.text;id=r.json()['id']
  assert c.post('/api/integration/import',json=body).status_code==409
  read=c.get('/api/integration/records/'+id).json();assert read['rows']==rows and read['manifest']==manifest
  assert Bridge(server.RUNTIME/'simulink_imports').read(id)['rows']==rows
  with zipfile.ZipFile(io.BytesIO(c.get('/api/integration/records/'+id+'/export').content)) as z:
   assert z.read('signals.csv')==text.encode('utf-8')
   for name,h in json.loads(z.read('hashes.json')).items():assert hashlib.sha256(z.read(name)).hexdigest()==h
  for change in ('unit','hash','count','nan','duplicate_time'):
   b=copy.deepcopy(body)
   if change=='unit':b['manifest']['units']['Pgrid_W']='kW'
   elif change=='hash':b['manifest']['csv_sha256']='bad'
   elif change=='count':b['manifest']['sample_count']-=1
   else:
    records=list(csv.DictReader(io.StringIO(text.lstrip('\ufeff'))));records[1]['Pgrid_W']='nan' if change=='nan' else records[1]['Pgrid_W']
    if change=='duplicate_time':records[1]['time_s']=records[0]['time_s']
    out=io.StringIO();w=csv.DictWriter(out,fieldnames=list(records[0]));w.writeheader();w.writerows(records);b['csv_text']=out.getvalue();b['manifest']['csv_sha256']=hashlib.sha256(b['csv_text'].encode()).hexdigest()
   assert c.post('/api/integration/preview',json=b).status_code==400,change
  assert c.get('/api/live/state').json()==live and c.get('/api/state').json()['dataset']==archive['dataset']
  p=server.RUNTIME/'simulink_imports'/id/'signals.csv';p.write_bytes(p.read_bytes()+b'edit');assert c.get('/api/integration/records/'+id).status_code==409 and c.get('/api/integration/records/'+id+'/export').status_code==409
 # An artificial large CSV fixture validates full sample retention, not a new simulation result.
 n=100001;out=io.StringIO();out.write('time_s,G_Wm2,Pgrid_W,Qgrid_var,Vdc_V,VdcRef_V\n')
 for i in range(n):out.write(f'{i*.00005:.5f},1000,60000,0,850,850\n')
 large=out.getvalue();m=copy.deepcopy(manifest);m.update(sample_count=n,csv_sha256=hashlib.sha256(large.encode()).hexdigest());r,_=validate(large,m);assert len(r)==n and r[-1]['time_s']==5
 bom='\ufeff'+text;m=copy.deepcopy(manifest);m['csv_sha256']=hashlib.sha256(bom.encode()).hexdigest();assert len(validate(bom,m)[0])==len(rows)
 print('PASS: paired CSV/manifest, exact raw export, source units/hash/count/clock gates, 100001-point retention, BOM, persistence, duplicate/tamper rejection, live isolation, GLB120 bindings and native blend fingerprints. MATLAB exporter NOT executed.')
if __name__=='__main__':run()
