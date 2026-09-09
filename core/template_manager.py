from __future__ import annotations

import json
from pathlib import Path

from core.paths import app_root


class TemplateManager:
    REQUIRED = {"name", "duration_per_image", "transition", "motion"}
    TRANSITIONS = {"fade", "slideleft", "none"}
    MOTIONS = {"zoom", "none"}

    def __init__(self, template_dir: Path | None = None) -> None:
        self.template_dir = template_dir or app_root() / "templates"

    def list_templates(self) -> list[str]:
        return sorted(path.stem for path in self.template_dir.glob("*.json"))

    def load(self, name: str) -> dict:
        safe_name = Path(name).stem
        path = self.template_dir / f"{safe_name}.json"
        if not path.is_file():
            raise ValueError(f"模板不存在：{name}")
        data = json.loads(path.read_text(encoding="utf-8"))
        missing = self.REQUIRED - data.keys()
        if missing:
            raise ValueError(f"模板缺少字段：{', '.join(sorted(missing))}")
        duration = float(data["duration_per_image"])
        if duration <= 0:
            raise ValueError("图片展示时间必须大于 0")
        if data["transition"] not in self.TRANSITIONS:
            raise ValueError("不支持的转场")
        if data["motion"] not in self.MOTIONS:
            raise ValueError("不支持的动画")
        data["duration_per_image"] = duration
        data["transition_duration"] = max(0.0, min(float(data.get("transition_duration", 0.35)), duration / 2))
        data["music_volume"] = max(0.0, min(float(data.get("music_volume", 0.65)), 1.0))
        return data
