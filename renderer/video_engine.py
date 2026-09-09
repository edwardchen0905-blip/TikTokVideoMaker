from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import math
from core.paths import app_root
from pathlib import Path
from typing import Callable, Iterable


Progress = Callable[[str], None]


def find_ffmpeg() -> str:
    bundled = app_root() / 'runtime' / 'ffmpeg.exe'
    if bundled.is_file():
        return str(bundled)
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError) as exc:
        raise RuntimeError("软件运行组件 FFmpeg 缺失，请使用完整便携包。") from exc


class VideoEngine:
    def __init__(self, ffmpeg: str | None = None, cache_root: Path | None = None) -> None:
        self.ffmpeg = ffmpeg or find_ffmpeg()
        self.cache_root = cache_root

    def generate(
        self,
        assets: Iterable[str | Path],
        template: dict,
        output: str | Path,
        music: str | Path | None = None,
        progress: Progress | None = None,
    ) -> Path:
        images = [Path(item).expanduser().resolve() for item in assets]
        if not images:
            raise ValueError("至少需要一张商品图片")
        missing = [str(path) for path in images if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"素材不存在：{missing[0]}")
        music_path = Path(music).expanduser().resolve() if music else None
        if music_path and not music_path.is_file():
            raise FileNotFoundError(f"音乐不存在：{music_path}")

        output_path = Path(output).expanduser().resolve()
        if output_path in images or output_path == music_path:
            raise ValueError("输出路径不能覆盖原始素材")
        if output_path.exists():
            raise ValueError("输出文件已存在")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        duration = float(template["duration_per_image"])
        transition_duration = float(template.get("transition_duration", 0.35))
        transition = str(template.get("transition", "fade"))
        if not math.isfinite(duration) or duration <= 0 or duration > 600:
            raise ValueError("素材时长必须介于 0 和 600 秒之间")
        if transition not in {'fade', 'slideleft', 'none'}:
            raise ValueError("不支持的转场")
        if not math.isfinite(transition_duration) or not 0 <= transition_duration < duration:
            raise ValueError("转场时间必须小于素材展示时间")

        with tempfile.TemporaryDirectory(prefix="tvm_render_", dir=self.cache_root) as temporary:
            work = Path(temporary)
            segments: list[Path] = []
            for index, image in enumerate(images, start=1):
                if progress:
                    progress(f"处理图片 {index}/{len(images)}")
                segment = work / f"segment_{index:04d}.mp4"
                self._make_segment(image, segment, duration, template.get("motion", "none"))
                segments.append(segment)

            silent_video = work / "video.mp4"
            if len(segments) == 1:
                shutil.copy2(segments[0], silent_video)
            elif transition == "none" or transition_duration <= 0:
                self._concat(segments, silent_video, work)
            else:
                self._crossfade(segments, silent_video, duration, transition_duration, transition)

            if progress:
                progress("写入 MP4")
            completed = work / 'completed.mp4'
            if music_path:
                self._add_music(silent_video, music_path, completed, float(template.get("music_volume", 0.65)))
            else:
                shutil.copy2(silent_video, completed)
            partial = output_path.with_suffix('.partial')
            try:
                shutil.copy2(completed, partial)
                partial.replace(output_path)
            finally:
                partial.unlink(missing_ok=True)
        if progress:
            progress("完成")
        return output_path

    def _run(self, command: list[str]) -> None:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:
            message = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "FFmpeg 执行失败"
            raise RuntimeError(message)

    def _make_segment(self, image: Path, output: Path, duration: float, motion: str) -> None:
        base = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=white,setsar=1"
        is_video = image.suffix.lower() in {'.mp4','.mov','.mkv','.webm'}
        video_filter = base
        if motion == "zoom" and not is_video:
            frames = max(1, round(duration * 30))
            video_filter += f",zoompan=z='min(zoom+0.0008,1.08)':d={frames}:s=1080x1920:fps=30"
        video_filter += ",format=yuv420p"
        self._run([
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *( ["-stream_loop", "-1"] if is_video else ["-loop", "1"]), "-i", str(image),
            "-vf", video_filter, "-t", str(duration), "-r", "30", "-an", "-c:v", "libx264",
            "-preset", "veryfast", "-movflags", "+faststart", str(output),
        ])

    def _concat(self, segments: list[Path], output: Path, work: Path) -> None:
        manifest = work / "concat.txt"
        manifest.write_text("".join(f"file '{path.as_posix()}'\n" for path in segments), encoding="utf-8")
        self._run([
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
            "-i", str(manifest), "-c", "copy", "-movflags", "+faststart", str(output),
        ])

    def _crossfade(
        self,
        segments: list[Path],
        output: Path,
        duration: float,
        transition_duration: float,
        transition: str,
    ) -> None:
        command = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        for segment in segments:
            command.extend(["-i", str(segment)])
        filters: list[str] = []
        previous = "0:v"
        for index in range(1, len(segments)):
            result = f"v{index}"
            offset = index * (duration - transition_duration)
            filters.append(
                f"[{previous}][{index}:v]xfade=transition={transition}:duration={transition_duration}:offset={offset}[{result}]"
            )
            previous = result
        command.extend([
            "-filter_complex", ";".join(filters), "-map", f"[{previous}]", "-an", "-c:v", "libx264",
            "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
        ])
        self._run(command)

    def _add_music(self, video: Path, music: Path, output: Path, volume: float) -> None:
        self._run([
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
            "-stream_loop", "-1", "-i", str(music), "-filter:a", f"volume={volume}", "-map", "0:v:0",
            "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
            "-movflags", "+faststart", str(output),
        ])


def probe_video(path: str | Path) -> dict:
    bundled = app_root() / 'runtime' / 'ffprobe.exe'
    ffprobe = str(bundled) if bundled.is_file() else shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("软件运行组件 FFprobe 缺失")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return json.loads(result.stdout) if result.returncode == 0 else {}
