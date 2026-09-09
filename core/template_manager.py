from __future__ import annotations
import json
import math
from pathlib import Path
from core.paths import app_root


def seconds(value, label='展示时间', maximum=600):
    if isinstance(value, bool):
        raise ValueError(label+'必须为有效秒数')
    try:
        value=float(value)
    except (TypeError, ValueError):
        raise ValueError(label+'必须为有效秒数') from None
    if not math.isfinite(value) or not 1/30 <= value <= maximum:
        raise ValueError(f'{label}必须介于 1/30 和 {maximum} 秒之间')
    return round(value*30)/30


class TemplateManager:
    REQUIRED={'name','duration_per_image','transition','motion'}
    TRANSITIONS={'fade','slideleft','slideright','slideup','slidedown','none'}
    MOTIONS={'zoom','pan','none'}

    def __init__(self, template_dir=None):
        self.template_dir=template_dir or app_root()/'templates'

    def list_templates(self):
        return sorted(p.stem for p in self.template_dir.glob('*.json'))

    @classmethod
    def validate(cls, data):
        data=dict(data)
        if cls.REQUIRED-data.keys():raise ValueError('模板缺少必要字段')
        if not str(data['name']).strip():raise ValueError('模板名称不能为空')
        data['duration_per_image']=seconds(data['duration_per_image'])
        if data['transition'] not in cls.TRANSITIONS:raise ValueError('不支持的转场')
        if data['motion'] not in cls.MOTIONS:raise ValueError('不支持的动画')
        transition=float(data.get('transition_duration',.35)) if data['transition']!='none' else 0
        volume=float(data.get('music_volume',.65))
        if not math.isfinite(transition) or transition<0 or transition>=data['duration_per_image']:raise ValueError('转场时间必须小于展示时间')
        if not math.isfinite(volume) or not 0<=volume<=1:raise ValueError('音量必须介于 0 和 1')
        data['transition_duration']=round(transition*30)/30 if data['transition']!='none' else 0
        data['music_volume']=volume
        data.setdefault('version',1)
        return data

    def load(self, name):
        if Path(name).name!=name:raise ValueError('模板名称无效')
        path=self.template_dir/f'{name}.json'
        if not path.is_file():raise ValueError('模板不存在：'+name)
        return self.validate(json.loads(path.read_text(encoding='utf-8')))
