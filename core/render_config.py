"""Shared effect choices and final rendering parameters; no production templates."""
from __future__ import annotations

import math


TRANSITIONS = [
    {'id': 'none', 'name': '直接切换', 'description': '上一张结束后直接显示下一张，不占用转场时间。'},
    {'id': 'fade', 'name': '淡入淡出', 'description': '上一张逐渐淡出，下一张逐渐显示。'},
    {'id': 'dissolve', 'name': '溶解', 'description': '画面中的像素逐步替换成下一张图片。'},
    {'id': 'slideleft', 'name': '左滑', 'description': '整幅画布向左滑动，切换到下一张。'},
    {'id': 'slideright', 'name': '右滑', 'description': '整幅画布向右滑动，切换到下一张。'},
    {'id': 'slideup', 'name': '上滑', 'description': '整幅画布向上滑动，切换到下一张。'},
    {'id': 'slidedown', 'name': '下滑', 'description': '整幅画布向下滑动，切换到下一张。'},
    {'id': 'coverleft', 'name': '推入', 'description': '下一张从右侧推入，逐渐覆盖上一张。'},
    {'id': 'zoomsafe', 'name': '缩放切换', 'description': '下一张由稍小等比放大至完整画布，同时淡入；不放大裁切原图。'},
]

RESOLUTIONS = [
    {'id': '1080x1920', 'name': 'TikTok 竖屏', 'ratio': '9:16', 'width': 1080, 'height': 1920, 'description': '默认尺寸；方形图片完整显示，上下允许留白。'},
    {'id': '1080x1080', 'name': '方形', 'ratio': '1:1', 'width': 1080, 'height': 1080, 'description': '适合方形画布；图片保持比例，空余位置留白。'},
    {'id': '1920x1080', 'name': '横屏', 'ratio': '16:9', 'width': 1920, 'height': 1080, 'description': '适合横向画布；图片完整显示，空余位置留白。'},
]


def seconds(value, label='展示时间', maximum=600):
    if isinstance(value, bool):
        raise ValueError(label+'必须为有效秒数')
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(label+'必须为有效秒数') from None
    if not math.isfinite(value) or not 1/30 <= value <= maximum:
        raise ValueError(f'{label}必须介于 1/30 和 {maximum} 秒之间')
    return round(value*30)/30


def validate_config(data):
    cfg = dict(data)
    # Historical tasks already contain their complete rendering configuration.
    # The single transition field is read only when no new selection is present.
    transitions = cfg.get('transitions', [cfg.get('transition', 'none')])
    if not isinstance(transitions, list) or not transitions or any(not isinstance(t, str) for t in transitions):
        raise ValueError('请至少选择一种转场效果')
    if set(transitions)-{t['id'] for t in TRANSITIONS}:
        raise ValueError('转场效果不存在，请重新选择')
    cfg['transitions'] = list(dict.fromkeys(transitions))
    cfg['duration_per_image'] = seconds(cfg.get('duration_per_image', 2))
    for key, default, maximum, label in (
        ('transition_duration', .3, 600, '转场时间'),
        ('music_volume', .65, 1, '音乐音量'),
    ):
        value = cfg.get(key, default)
        if isinstance(value, bool):
            raise ValueError(label+'必须为有效数字')
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError(label+'必须为有效数字') from None
        if not math.isfinite(value) or not 0 <= value <= maximum:
            raise ValueError(f'{label}必须介于 0 和 {maximum} 之间')
        cfg[key] = round(value*30)/30 if key == 'transition_duration' else value
    cfg.setdefault('motion', 'none')
    if cfg['motion'] not in {'none', 'zoom', 'pan'}:
        raise ValueError('画面运动方式无效')
    cfg.setdefault('resolution', '1080x1920')
    resolution = next((r for r in RESOLUTIONS if r['id'] == cfg['resolution']), None)
    if not resolution:
        raise ValueError('请选择有效的视频尺寸')
    cfg.update({key: resolution[key] for key in ('width', 'height', 'ratio')})
    cfg.setdefault('fit', 'contain')
    if cfg['fit'] != 'contain':
        raise ValueError('当前仅支持完整显示：等比例、不裁切、不拉伸')
    if 'expected_duration' in cfg:
        value = cfg['expected_duration']
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError('保存的视频总时长无效')
    return cfg


def transition_plan(cfg, n):
    if type(n) is not int or n < 1:
        raise ValueError('至少需要一份图片或视频素材')
    cfg = validate_config(cfg)
    selected = cfg['transitions']
    duration = cfg['transition_duration']
    sequence = [selected[i % len(selected)] if duration else 'none' for i in range(n-1)]
    return sequence, [duration if effect != 'none' else 0 for effect in sequence]
