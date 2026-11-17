"""Generate reproducible synthetic scan, geometric shading cases and DC comparison."""
from pathlib import Path
import json,csv
from spatial_model import scan,shading,pcd_text
from pv_physics import solve

def main():
 folder=Path(__file__).resolve().parent/'spatial_examples';folder.mkdir(exist_ok=True)
 config={'obstacle_height':0.,'stations':4,'azimuth_samples':720,'vertical_samples':64,'max_range':100.,'range_noise':.02,'dropout':.05,'seed':42}
 r=scan(config);(folder/'synthetic_first_returns.pcd').write_text(pcd_text(r),encoding='utf-8');(folder/'scan_metadata.json').write_text(json.dumps({k:v for k,v in r.items() if k!='points'},ensure_ascii=False,indent=2),encoding='utf-8')
 env={'sun_azimuth':0.,'sun_elevation':15.,'dni':800.,'diffuse_poa':100.,'temperature':25.,'obstacle_height':0.,'samples':3};summaries=[]
 for name,config in [('low_sun',{**env,'sun_elevation':5.}),('mid_sun',env),('high_sun',{**env,'sun_elevation':40.}),('obstacle',{**env,'obstacle_height':10.})]:
  geom=shading(config);r=solve(geom['physics_config']);clear=solve({**geom['physics_config'],'shade':[[1.]*20 for _ in range(6)]});summary={'case':name,'elevation_deg':config['sun_elevation'],'obstacle_height_m':config['obstacle_height'],'beam_blocked_mean':sum(sum(x) for x in geom['beam_blocked_fraction'])/120,'dc_pmp_W':r['pmp_W'],'no_occlusion_reference_W':clear['pmp_W'],'difference_W':clear['pmp_W']-r['pmp_W']};summaries.append(summary)
  (folder/(name+'_geometry.json')).write_text(json.dumps(geom,ensure_ascii=False,indent=2),encoding='utf-8')
 with (folder/'geometry_dc_comparison.csv').open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(summaries[0]));w.writeheader();w.writerows(summaries)
 print(json.dumps(summaries,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
