"""Record persistence, isolation and export integrity through actual API routes."""
import csv, hashlib, io, json, os, tempfile, zipfile
from pathlib import Path
TEST_RUNTIME=tempfile.TemporaryDirectory()
os.environ['WG_RUNTIME_DIR']=TEST_RUNTIME.name
from fastapi.testclient import TestClient
import server as m
from record_store import RecordStore


def checked_zip(response):
    assert response.status_code==200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        assert z.testzip() is None
        manifest=json.loads(z.read('manifest.json'))
        for f in manifest['files']:
            raw=z.read(f['file'])
            assert len(raw)==f['bytes'] and hashlib.sha256(raw).hexdigest()==f['sha256']
        return manifest,{name:z.read(name) for name in z.namelist()}


def test_records():
    with TestClient(m.app) as c:
        original=c.get('/api/live/state').json()
        assets=c.get('/api/provenance').json()['items']
        assert len(assets)==9 and len({a['id'] for a in assets})==9
        assert '未认证kW' in next(a for a in assets if a['id']=='forecast')['units']
        assert '未认证kWh' in next(a for a in assets if a['id']=='energy')['units']
        for a in assets:
            exported=c.get('/api/provenance/'+a['id']+'/export').json()
            assert exported['asset']==a
            assert all(len(f['sha256'])==64 for f in a['files'])
        assert c.get('/api/provenance/not_found/export').status_code==404
        current=c.get('/api/records').json()['items'][0]
        assert current['current'] and current['id']==original['run_id']
        detail=c.get('/api/records/'+current['id']).json()
        assert detail['summary']['samples']==0
        assert detail['summary']['max_balance_residual_kw'] is None
        assert c.get('/api/live/state').json()['seq']==original['seq']
        assert c.get('/api/live/state').json()['running'] is False
        # Inject actual numerical steps without relying on wall timing; the paused worker cannot race.
        with m.LIVE.lock:
            for _ in range(4):
                row=m.LIVE.model.step()
                m.LIVE.writer.writerow({k:row[k] for k in m.LIVE.writer.fieldnames})
            m.LIVE.file.flush()
        changed={**m.LIVE.model.config,'load_kw':45}
        c.post('/api/live/config',json={'config':changed,'reset':False})
        detail=c.get('/api/records/'+current['id']).json()
        assert detail['summary']['latest_requested_config']['load_kw']==45
        assert detail['summary']['initial_config']['load_kw']==30
        assert detail['summary']['samples']==4 and detail['events'][-1]['action']=='live_input'
        manifest,files=checked_zip(c.get('/api/records/'+current['id']+'/export'))
        rows=list(csv.DictReader(io.StringIO(files['telemetry.csv'].decode())))
        assert len(rows)==4 and manifest['summary']['samples']==4
        assert abs(float(rows[-1]['time_s'])-.4)<1e-9
        assert manifest['metadata']['code_files'][0]['sha256']==hashlib.sha256((m.ROOT/'live_simulation.py').read_bytes()).hexdigest()
        old_id=current['id']
        c.post('/api/live/control',json={'action':'reset'})
        old=c.get('/api/records/'+old_id).json()
        assert not old['summary']['current'] and old['summary']['samples']==4
        assert old['summary']['latest_requested_config']['load_kw']==45
        # Persist full successful and infeasible plans; no live or replay mutation.
        before=c.get('/api/live/state').json()
        plan=c.post('/api/storage/solve',json=m.StorageInput().checked()).json()
        id_=plan['saved_record']['id']
        failed=c.post('/api/storage/solve',json={**m.StorageInput().checked(),'scenario':'grid_pressure','grid_import_kw':0,'capacity_kwh':1,'charge_kw':0,'discharge_kw':0}).json()
        failed_detail=c.get('/api/records/'+failed['saved_record']['id']).json()
        assert failed_detail['summary']['objective_currency'] is None
        assert c.get('/api/live/state').json()==before
        reopened=RecordStore(m.ROOT,m.RUNTIME,m.LIVE)
        restored=reopened.capture(id_)[0]
        assert restored['metadata']['result']['config_sha256']==plan['config_sha256']
        assert restored['metadata']['result']['strategies']==plan['strategies']
        manifest,files=checked_zip(c.get('/api/records/'+id_+'/export'))
        saved=json.loads(files['plan.json'])
        assert saved['result']['config']==plan['config'] and manifest['summary']['intervals']==24
        invalid=c.get('/api/records/storage_not_an_id');assert invalid.status_code==404
        assert c.get('/api/records/'+'f'*12+'/export').status_code==404
        # Malformed storage records are visible as warnings; valid records still load.
        bad=m.RECORDS.plans/('storage_'+'e'*16+'.json');bad.write_text('{',encoding='utf-8')
        listing=c.get('/api/records').json()
        assert any(r['id']==id_ for r in listing['items']) and listing['warnings']
        assert c.get('/api/records/'+bad.stem).status_code==409
        # Imported files are included with exact bytes, without pretending provenance was verified.
        text='time_s,G_Wm2,Pgrid_W,Qgrid_var,Vdc_V,VdcRef_V\n0,900,1,0,850,850\n1,900,2,0,850,850\n'
        imported=c.post('/api/import',json={'title':'record test','csv_text':text}).json()
        registry=c.get('/api/provenance').json()['items']
        a=next(a for a in registry if a['id']=='archive_'+imported['id'])
        assert a['mode']=='用户导入' and a['files'][0]['sha256']==hashlib.sha256(text.encode()).hexdigest()
    print('PASS: exact provenance hashes, imported-source qualification, read-only paused inspection, input/initial configuration separation, coherent live ZIP hashes, historical run retention, persisted/reopened complete feasible and infeasible plans, invalid ID rejection and corrupt-record isolation.')


if __name__=='__main__':test_records()
