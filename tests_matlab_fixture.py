"""TEST ONLY: reuse an uploaded capture to exercise process/result plumbing.
This executable is not MATLAB and is never selected by production discovery.
"""
import os,sys,json,time,shutil
from pathlib import Path
mode=sys.argv[1];root=Path(sys.argv[2]);folder=Path(os.environ['WG_JOB_DIR']);request=json.loads((folder/'request.json').read_text())
print('TEST FIXTURE PROCESS — NOT MATLAB; no new simulation',flush=True)
time.sleep(10 if mode=='hang' else .4)
if mode=='fail':print('fixture failure',flush=True);sys.exit(7)
p=folder/'workspace/runs/fixture';p.mkdir(parents=True)
if mode=='captured':report={'web_ready':False,'reason':'TEST FIXTURE clock mismatch'}
else:
 key='inc' if request['algorithm']=='inc' else 'po';source=root/'data/simulink_comparison'/key
 shutil.copyfile(source/'signals.csv',p/'web_signals.csv');m=json.loads((source/'source.json').read_text());m['test_fixture']='reused uploaded capture; NOT a new MATLAB result'
 if mode=='bad':m['run']['input_sha256']='wrong'
 (p/'web_source.json').write_text(json.dumps(m));report={'web_ready':True,'reason':'TEST FIXTURE'}
(p/'report.json').write_text(json.dumps(report))
(folder/'receipt.json').write_text(json.dumps({'task_id':request['id'],'folder':str(p)}))
