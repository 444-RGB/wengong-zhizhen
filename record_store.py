"""Read-only provenance and durable run snapshots. No numerical model changes."""
from pathlib import Path
from datetime import datetime, timezone
from functools import lru_cache
import csv, hashlib, io, json, re, threading, uuid, zipfile


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')


@lru_cache(maxsize=256)
def _file_hash(path, size, modified_ns):
    return digest(Path(path).read_bytes())


def file_info(path, label):
    s = path.stat()
    return {'file': label, 'bytes': s.st_size, 'sha256': _file_hash(str(path), s.st_size, s.st_mtime_ns)}


def registry(root, sets):
    items = []
    for key, ds in sets.items():
        e, rows = ds['entry'], ds['rows']
        items.append({'id': 'archive_' + key, 'title': e['title'], 'kind': '归档数据', 'page': 'analysis',
                      'mode': '用户导入' if e.get('imported') else 'Simulink输出归档',
                      'summary': e['source'], 'clock': f"{rows[0]['time_s']:g}—{rows[-1]['time_s']:g} 模拟秒 / {len(rows)}样本",
                      'units': 's · W/m² · W · var · V', 'method': e.get('metadata', {}).get('algorithm', '导入文件未提供控制算法'),
                      'boundary': '独立回放；不代表实时设备测量或现场校准。导入来源未独立核验。' if e.get('imported') else '原信号每20点抽取到1ms；诊断配置对照，不是改进MPPT或最优参数证明。',
                      'parameters': e.get('metadata', {}), 'files': [file_info(ds['path'], ('runtime/' if e.get('imported') else 'data/') + e['file'])]})
    definitions = [
        ('live', '实时简化仿真', '数值模型', 'live', '后端独立推进', '六分区光伏、温升、逆变响应、电池与供需平衡。', '独立模拟秒 / 最大子步0.1s', 'kW · kWh · °C · W/m²', 'six-zone-reduced-dynamic-v1', '合成参数、理想分区MPPT、假设热惯性；非硬实时、EMT或Simulink联机。', ['live_simulation.py', 'pv_physics.py']),
        ('dc', 'DC物理实验', '数值模型', 'laboratory', '静态求解', '逐片受光、组串失配、旁路近似与多峰功率曲线。', '独立静态工况 / 无动态时钟', 'V · A · W · W/m² · °C', '单二极管 / De Soto辅助关系', '示例组件参数；未模拟电池片热点、动态控制或现场标定。', ['pv_physics.py']),
        ('spatial', '空间与遮挡', '几何模型', 'spatial', '合成已知几何', '最近交点射线扫描、噪声/丢点与直射光遮挡。', '已知位姿 / 独立静态工况', 'm · 无量纲遮挡比例', 'BVH最近命中 / 几何遮挡映射到DC', '不是实测点云、SLAM或现场重建；与实时六分区模型不等价。', ['spatial_model.py']),
        ('forecast', '功率预测归档', '研究归档', 'forecast', '2018封存结果', '逐时查看既有V28相关归档和独立事后评分。', '2018源时钟 / 未认证UTC', '源功率单位 / 未认证kW', 'Cross重建 + GRU + LightGBM固定融合 / 低发电轮廓回退', '2018已开封；仅历史回放，不是在线推理，也未接入电站或储能。', ['data/forecast/year_predictions.csv', 'data/forecast/interface_metadata.json', 'data/forecast/test_protocol.json']),
        ('energy', '区间积分研究归档', '研究归档', 'energy', '2017开发段', '三种子归档；未来1小时复合梯形积分目标。', '2017年9—12月源时钟 / 未认证UTC', '源功率单位×h / 未认证kWh', 'V30区间目标融合与直接目标树对照', '时间区间不是置信区间；不是最新论文实验或在线模型。', ['data/energy/forecast_interface_metadata.json', 'data/energy/protocol.json', *[f'data/energy/{s}_predictions.csv' for s in (20261026, 20261027, 20261028)]]),
        ('storage', '合成储能规划', '优化模型', 'storage', '确定性优化', '同输入比较无储能、储备规则与MILP。', '24个已知合成区间 / 每区间1h', 'kWh · kW · 示例计价单位', 'V29 SciPy / HiGHS MILP', '未认证的源预测单位不接入kWh规划；成本差不是实际收益。', ['dispatch_milp.py', 'storage_sandbox.py', 'source_records/dispatch_contract.json']),
    ]
    for id_, title, kind, page, mode, summary, clock, units, method, boundary, paths in definitions:
        items.append(dict(id=id_, title=title, kind=kind, page=page, mode=mode, summary=summary, clock=clock,
                          units=units, method=method, boundary=boundary, files=[file_info(root / p, p) for p in paths]))
    return {'release': 'V24', 'captured_at_utc': utc_now(), 'items': items,
            'scope': '文件指纹标识当前安装包或导入文件的字节；不等同于第三方认证。各模块时钟与单位独立。'}


class RecordStore:
    def __init__(self, root, runtime, live):
        self.root, self.runtime, self.live = root, runtime, live
        self.plans = runtime / 'storage_runs'
        self.plans.mkdir(exist_ok=True)
        self.lock = threading.RLock()

    def save_plan(self, result):
        id_ = 'storage_' + uuid.uuid4().hex[:16]
        entry = {'schema_version': 1, 'id': id_, 'kind': 'storage', 'created_at_utc': utc_now(),
                 'model': 'V29 deterministic synthetic MILP / 24 known one-hour intervals',
                 'code_files': [file_info(self.root / p, p) for p in ('dispatch_milp.py', 'storage_sandbox.py')],
                 'units': 'kWh · kW · 示例计价单位', 'clock': '24个已知合成区间，每区间1h',
                 'result': result}
        data = json_bytes(entry)
        with self.lock:
            temp = self.plans / (id_ + '.tmp')
            temp.write_bytes(data)
            temp.replace(self.plans / (id_ + '.json'))
        return {'id': id_, 'created_at_utc': entry['created_at_utc'], 'record_sha256': digest(data)}

    def _path(self, id_):
        if re.fullmatch(r'[0-9a-f]{12}', id_):
            return self.live.directory / (id_ + '.json'), 'live'
        if re.fullmatch(r'storage_[0-9a-f]{16}', id_):
            return self.plans / (id_ + '.json'), 'storage'
        raise FileNotFoundError('运行记录不存在')

    def _read(self, id_):
        path, kind = self._path(id_)
        if path.is_symlink():
            raise FileNotFoundError('运行记录不存在')
        return path, kind, json.loads(path.read_text(encoding='utf-8-sig'))

    def list(self):
        items, warnings = [], []
        with self.live.lock, self.lock:
            candidates = [p for p in self.live.directory.glob('*.json') if re.fullmatch(r'[0-9a-f]{12}', p.stem)] + list(self.plans.glob('storage_*.json'))
            candidates.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
            for p in candidates[:100]:
                try:
                    _, kind, data = self._read(p.stem)
                    current = kind == 'live' and p.stem == self.live.model.run_id
                    status = ('运行中' if self.live.running else '当前已暂停') if current else '历史记录'
                    if kind == 'storage':
                        result = data['result']
                        status = '约束复核通过' if result['strategies']['milp'].get('validation', {}).get('passed') else '不可行或未完成'
                    items.append({'id': p.stem, 'kind': kind, 'status': status, 'current': current,
                                  'title': '实时虚拟仿真' if kind == 'live' else '合成储能 · ' + {'sunny':'晴天基准','cloud_dip':'云影扰动','low_solar':'低辐照','standby':'待机','grid_pressure':'并网受限'}.get(data['result']['config']['scenario'],data['result']['config']['scenario']),
                                  'created_at_utc': data.get('created_at_utc'),
                                  'time_s': self.live.model.t if current else None,
                                  'config_sha256': data.get('result', {}).get('config_sha256')})
                except (OSError, ValueError, KeyError, TypeError) as e:
                    warnings.append({'id': p.stem, 'message': '记录不完整，无法读取；原文件保留。'})
        return {'items': items, 'total_files': len(candidates), 'limit': 100, 'warnings': warnings,
                'scope': '实时CSV与完整储能方案保存在本机；历史CSV可查看和导出，不自动恢复引擎状态。V14之前未保存的完整规划不能补建。'}

    def capture(self, id_):
        with self.live.lock, self.lock:
            path, kind, meta = self._read(id_)
            captured = utc_now()
            if kind == 'storage':
                raw = path.read_bytes()
                result = meta['result']
                detail = {'id': id_, 'kind': kind, 'captured_at_utc': captured, 'metadata': meta,
                          'summary': {'status': result['strategies']['milp']['status'], 'config_sha256': result['config_sha256'],
                                      'intervals': len(result['fixture']['dt_hours']),
                                      'validation': result['strategies']['milp'].get('validation'),
                                      'objective_currency': result['strategies']['milp'].get('objective_currency')},
                          'files': [{'file': 'plan.json', 'bytes': len(raw), 'sha256': digest(raw)}]}
                return detail, {'plan.json': raw}
            current = id_ == self.live.model.run_id
            if current and not self.live.file.closed:
                self.live.file.flush()
            raw = path.with_suffix('.csv').read_bytes()
            event_path = self.live.directory / (id_ + '_events.jsonl')
            events_raw = event_path.read_bytes() if event_path.exists() else b''
            events = [json.loads(line) for line in events_raw.decode('utf-8').splitlines() if line.strip()]
            rows = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
            count, last, balance, battery = 0, None, 0., 0.
            for row in rows:
                count += 1
                last = row
                balance = max(balance, abs(float(row['balance_residual_kw'])))
                battery = max(battery, abs(float(row['battery_residual_kwh'])))
            config = dict(meta.get('initial_config', {}))
            for event in events:
                if event['action'] in ('live_input', 'reset_config'):
                    config = dict(event['body'])
            state = self.live.state() if current else None
            summary = {'samples': count, 'last_sample_time_s': float(last['time_s']) if last else None,
                       'last_soc': float(last['soc']) if last else None, 'max_balance_residual_kw': balance if count else None,
                       'max_battery_residual_kwh': battery if count else None, 'current': current,
                       'status': ('运行中' if state['running'] else '当前已暂停') if state else '历史CSV / 未恢复状态',
                       'latest_requested_config': dict(self.live.model.config) if current else config,
                       'initial_config': meta.get('initial_config', {}), 'event_count': len(events)}
            # Metadata and both data files are captured under the same engine lock.
            detail = {'id': id_, 'kind': kind, 'captured_at_utc': captured, 'metadata': meta, 'summary': summary,
                      'events': events, 'units': '功率kW / 电量kWh / 时间s / 温度°C',
                      'scope': '文件是该次运行的数值记录；输入变更见事件时间。暂停、重启或新运行不等同于完成实验。',
                      'files': [{'file': name, 'bytes': len(data), 'sha256': digest(data)} for name, data in [('telemetry.csv', raw), ('events.jsonl', events_raw)]]}
            return detail, {'telemetry.csv': raw, 'events.jsonl': events_raw, 'initial_metadata.json': json_bytes(meta)}

    def export(self, id_):
        detail, files = self.capture(id_)
        manifest = dict(detail)
        manifest['export_note'] = '导出时重新冻结，运行中样本可能多于先前页面。SHA256对应本ZIP内文件；不是签名或认证。'
        manifest['files'] = [{'file': name, 'bytes': len(data), 'sha256': digest(data)} for name, data in files.items()]
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            for name, data in files.items():
                z.writestr(name, data)
            z.writestr('manifest.json', json_bytes(manifest))
        return out.getvalue()
