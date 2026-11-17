"""Recognize explicit simulation setpoints in the user's current message."""
import re
import unicodedata


NUMERIC = {
    '辐照': ('irradiance', r'W\s*/\s*m(?:\^?2)'),
    '辐照度': ('irradiance', r'W\s*/\s*m(?:\^?2)'),
    '无功电流参考': ('iq_ref_pu', r'pu'),
    '组件温度': ('module_temperature', r'°?C|度'),
    '环境温度': ('ambient', r'°?C|度'),
    '风速': ('wind', r'm\s*/\s*s|米/秒'),
    '湿度': ('humidity', '%'),
    '相对湿度': ('humidity', '%'),
    '沙尘光学厚度': ('dust_optical_depth', ''),
    '积灰': ('soiling_pct', '%'),
    '积灰损失': ('soiling_pct', '%'),
    '积灰损失率': ('soiling_pct', '%'),
    '负荷': ('load_kw', 'kW'),
}
LABELS = {field: label for label, (field, _) in NUMERIC.items()}
LABELS.update(cloud='云团扰动', temperature_mode='温度模式')
UNITS = {
    'irradiance': 'W/m²',
    'iq_ref_pu': 'pu',
    'module_temperature': '℃',
    'ambient': '℃',
    'wind': 'm/s',
    'humidity': '%',
    'soiling_pct': '%',
    'load_kw': 'kW',
}


def describe_setpoints(patch):
    values = []
    for field, value in patch.items():
        if field == 'cloud':
            display = '开启' if value else '关闭'
        elif field == 'temperature_mode':
            display = '固定' if value else '自动'
        else:
            display = f'{value:g} {UNITS.get(field, "")}'.rstrip()
        values.append(f'{LABELS[field]} {display}')
    return '、'.join(values)


PREFIX = r'(?:请(?:帮我)?|帮我)?\s*(?:(?:把|将)\s*)?'
SET = r'\s*(?:修改为|调整为|调节到|调节为|设定为|设置为|改成|改为|改到|设为|设成|调到|调为|=)\s*'
NUMBER = r'([+-]?(?:\d{1,8}(?:\.\d{1,8})?|\.\d{1,8}))'


def parse_setpoints(question):
    text = unicodedata.normalize('NFKC', question).strip().rstrip('。！!')
    clauses = re.split(r'\s*(?:[,、;]|并且|并(?=把|将|开启|关闭)|和(?=把|将))\s*', text)
    patch = {}
    for clause in clauses:
        cloud = re.fullmatch(PREFIX + r'(开启|打开|关闭|关掉)\s*云团(?:扰动)?', clause)
        auto = re.fullmatch(PREFIX + r'温度模式' + SET + r'自动', clause)
        matched = None
        if cloud:
            matched = ('cloud', cloud[1] in ('开启', '打开'))
        elif auto:
            matched = ('temperature_mode', 0)
        else:
            for label, (field, unit) in NUMERIC.items():
                suffix = fr'\s*(?:{unit})?' if unit else ''
                match = re.fullmatch(PREFIX + label + SET + NUMBER + suffix, clause, re.I)
                if match:
                    matched = (field, float(match[1]))
                    break
        if matched is None:
            return None
        field, value = matched
        if field in patch:
            raise ValueError('同一条消息只填写一次' + LABELS[field])
        patch[field] = value
    if 'module_temperature' in patch:
        if 'temperature_mode' in patch:
            raise ValueError('固定组件温度与自动温度模式不能同时设置')
        patch['temperature_mode'] = 1
    return patch
