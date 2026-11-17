"""Optional reproducible scientific preview. Requires matplotlib + numpy only for plotting."""
from pathlib import Path
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
folder=Path(__file__).resolve().parent/'spatial_examples';meta=json.loads((folder/'scan_metadata.json').read_text(encoding='utf-8'))
lines=(folder/'synthetic_first_returns.pcd').read_text(encoding='utf-8').split('DATA ascii\n')[1].splitlines();p=np.array([[float(x) for x in line.split()] for line in lines]);ground=p[p[:,3]==0][::8];objects=p[p[:,3]!=0]
fig=plt.figure(figsize=(14,8),facecolor='#080d10');ax=fig.add_subplot(111,projection='3d',facecolor='#080d10')
ax.scatter(ground[:,0],ground[:,1],ground[:,2],c='#435144',s=.35,alpha=.45,linewidths=0)
c=ax.scatter(objects[:,0],objects[:,1],objects[:,2],c=objects[:,2],cmap='turbo',vmin=0,vmax=6,s=1.4,alpha=.95,linewidths=0)
ax.view_init(elev=43,azim=-65);ax.set_box_aspect([100,76,28]);ax.set_xlim(-48,48);ax.set_ylim(-36,35);ax.set_zlim(-.1,7)
ax.set_xlabel('X / m',color='#91a79b');ax.set_ylabel('Y / m',color='#91a79b');ax.set_zlabel('Height / m',color='#91a79b');ax.tick_params(colors='#7f948a',labelsize=8)
for axis in [ax.xaxis,ax.yaxis,ax.zaxis]:axis.set_pane_color((.04,.065,.07,1));axis._axinfo['grid']['color']=(.15,.21,.2,.3)
fig.suptitle('Synthetic PV station | first-return ray scan',color='#d5e5d7',fontsize=20,y=.96)
fig.text(.5,.07,f"{len(meta['poses'])} known scan poses | {meta['rays']:,} rays | {meta['point_count']:,} retained points | ground preview sampled 1:8",color='#8fa597',ha='center',fontsize=10)
fig.text(.5,.035,'SIMULATION OUTPUT  /  simplified geometry  /  no real survey, SLAM or reconstructed geometry',color='#b99e69',ha='center',fontsize=9)
cb=fig.colorbar(c,ax=ax,shrink=.48,pad=.04);cb.set_label('Height / m',color='#a9baa9',fontsize=9);cb.ax.tick_params(colors='#819880',labelsize=8)
fig.savefig(folder/'pointcloud_preview.png',dpi=160,facecolor=fig.get_facecolor(),bbox_inches='tight');plt.close(fig)
