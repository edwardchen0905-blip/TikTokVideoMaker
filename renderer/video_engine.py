from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import math
from fractions import Fraction
from core.paths import app_root
from core.render_config import validate_config, transition_plan, seconds
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
        config: dict,
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
        config = validate_config(config)
        durations = config.get('durations', [config['duration_per_image']]*len(images))
        if not isinstance(durations, list) or len(durations) != len(images):
            raise ValueError("逐图时长与素材数量不匹配或无效")
        durations = [seconds(d) for d in durations]
        sequence, overlaps = transition_plan(config, len(images))
        for key, actual in (('transition_sequence', sequence), ('transition_durations', overlaps)):
            if key in config and config[key] != actual:
                raise ValueError('保存的转场顺序或时间与任务配置不一致')
        if any(overlap >= min(durations[i], durations[i+1]) for i, overlap in enumerate(overlaps)):
            raise ValueError('转场时间必须小于相邻图片展示时间')
        expected = sum(durations)-sum(overlaps)
        if not math.isfinite(expected) or expected <= 0:
            raise ValueError('视频总时长无效')
        if 'expected_duration' in config and abs(float(config['expected_duration'])-expected) > 1/60:
            raise ValueError('保存的视频总时长与逐图时间、转场配置不一致')

        with tempfile.TemporaryDirectory(prefix="tvm_render_", dir=self.cache_root) as temporary:
            work = Path(temporary)
            segments: list[Path] = []
            for index, image in enumerate(images, start=1):
                if progress:
                    progress(f"处理图片 {index}/{len(images)}")
                segment = work / f"segment_{index:04d}.mp4"
                self._make_segment(image, segment, durations[index-1], config['motion'], config['width'], config['height'])
                segments.append(segment)

            silent_video = work / "video.mp4"
            if len(segments) == 1:
                shutil.copy2(segments[0], silent_video)
            elif not any(overlaps):
                self._concat(segments, silent_video, work)
            else:
                self._crossfade(segments, silent_video, durations, overlaps, sequence)

            if progress:
                progress("写入 MP4")
            completed = work / 'completed.mp4'
            if music_path:
                self._add_music(silent_video, music_path, completed, config['music_volume'], config.get('music_fade', 0), expected, config.get('music_playback', 'loop'))
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
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0), timeout=3600)
        if result.returncode:
            message = '\n'.join(result.stderr.strip().splitlines()[-12:]) if result.stderr.strip() else "FFmpeg 执行失败"
            raise RuntimeError(message)

    def _make_segment(self, image: Path, output: Path, duration: float, motion: str, width=1080, height=1920) -> None:
        is_video = image.suffix.lower() in {'.mp4','.mov','.mkv','.webm'}
        # Motion reserves a safe inset first; the entire image stays inside the canvas.
        inset = .9 if motion in {'zoom', 'pan'} else 1
        size = f'{2*int(width*inset/2)}:{2*int(height*inset/2)}'
        canvas = f'{width}x{height}'
        video_filter = f"scale={size}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=white,setsar=1"
        if motion == 'zoom':
            frames=max(1,round(duration*30))
            video_filter += f",zoompan=z='1+0.08*min(on/{frames},1)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={1 if is_video else frames}:s={canvas}:fps=30"
        elif motion == 'pan':
            frames=max(1,round(duration*30))
            video_filter += f",zoompan=z=1.04:x='iw/2-iw/zoom/2+{width/108}*sin(2*PI*on/{frames})':y='ih/2-ih/zoom/2':d={1 if is_video else frames}:s={canvas}:fps=30"
        elif motion != 'none':
            raise ValueError('不支持的动画')
        video_filter += ",format=yuv420p"
        self._run([
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *( ["-stream_loop", "-1"] if is_video else ["-loop", "1"]), "-i", str(image),
            "-vf", video_filter, "-t", str(duration), "-r", "30", "-an", "-c:v", "libx264",
            "-preset", "veryfast", "-movflags", "+faststart", str(output),
        ])

    def _concat(self, segments: list[Path], output: Path, work: Path) -> None:
        manifest = work / "concat.txt"
        manifest.write_text("".join("file '"+path.as_posix().replace("'", "'\\''")+"'\n" for path in segments), encoding="utf-8")
        self._run([
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
            "-i", str(manifest), "-c", "copy", "-movflags", "+faststart", str(output),
        ])

    def _crossfade(
        self,
        segments: list[Path],
        output: Path,
        duration: list[float],
        overlaps: list[float],
        transitions: list[str],
    ) -> None:
        # Two inputs per merge keep large batches from opening every clip at once.
        previous=segments[0]
        elapsed=duration[0]
        for index, segment in enumerate(segments[1:], 1):
            target=output if index==len(segments)-1 else output.parent/f'merge_{index}.mp4'
            overlap = overlaps[index-1]
            effect = transitions[index-1]
            if not overlap:
                self._concat([previous, segment], target, output.parent)
            else:
                transition = 'custom' if effect == 'zoomsafe' else effect
                video_filter = f'[0:v][1:v]xfade=transition={transition}:duration={overlap}:offset={elapsed-overlap}'
                if effect == 'zoomsafe':
                    # Sample the incoming image at 75%-100% of the canvas. Unlike
                    # FFmpeg's zoomin, this never enlarges/crops the source image.
                    scale = '(1-0.25*P)'
                    x, y = f'(X-W/2)/{scale}+W/2', f'(Y-H/2)/{scale}+H/2'
                    inside = f'gte(X,W*(1-{scale})/2)*lt(X,W*(1+{scale})/2)*gte(Y,H*(1-{scale})/2)*lt(Y,H*(1+{scale})/2)'
                    sample = f'if(eq(PLANE,0),b0({x},{y}),if(eq(PLANE,1),b1({x},{y}),b2({x},{y})))'
                    video_filter += f":expr='A*P+if({inside},{sample},if(eq(PLANE,0),235,128))*(1-P)'"
                self._run([self.ffmpeg,'-hide_banner','-loglevel','error','-y','-i',str(previous),'-i',str(segment),
                    '-filter_complex_threads','1','-filter_complex',video_filter+'[v]',
                    '-map','[v]','-an','-c:v','libx264','-preset','veryfast','-pix_fmt','yuv420p','-r','30','-movflags','+faststart',str(target)])
            if previous not in segments:previous.unlink()
            previous=target
            elapsed+=duration[index]-overlap

    def _add_music(self, video: Path, music: Path, output: Path, volume: float, fade=0, duration=0, playback='loop') -> None:
        audio_filter=f"volume={volume},apad"
        if fade:audio_filter+=f",afade=t=in:d={fade},afade=t=out:st={max(0,duration-fade)}:d={fade}"
        self._run([
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
            *(["-stream_loop", "-1"] if playback=="loop" else []), "-i", str(music), "-filter:a", audio_filter, "-map", "0:v:0",
            "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
            "-t", str(duration), "-movflags", "+faststart", str(output),
        ])


def probe_video(path: str | Path) -> dict:
    bundled = app_root() / 'runtime' / 'ffprobe.exe'
    ffprobe = str(bundled) if bundled.is_file() else shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("软件运行组件 FFprobe 缺失")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=60,
    )
    return json.loads(result.stdout) if result.returncode == 0 else {}


def validate_output(path, config):
    config = validate_config(config)
    path=Path(path)
    if not path.is_file() or not path.stat().st_size:raise ValueError('输出文件缺失或为空')
    info=probe_video(path)
    video=next((s for s in info.get('streams',[]) if s.get('codec_type')=='video'),None)
    audio=[s for s in info.get('streams',[]) if s.get('codec_type')=='audio']
    if not video or video.get('codec_name')!='h264' or (video.get('width'),video.get('height'))!=(config['width'],config['height']):raise ValueError('输出编码或分辨率不符合任务')
    if video.get('sample_aspect_ratio') not in {'1:1',None}:raise ValueError('输出像素比例不符合任务')
    if Fraction(video.get('avg_frame_rate','0'))!=30:raise ValueError('输出帧率不是 30fps')
    if 'mp4' not in info.get('format',{}).get('format_name',''):raise ValueError('输出不是 MP4')
    actual=float(video.get('duration',info['format'].get('duration',0)))
    expected=config['expected_duration']
    if not math.isfinite(actual) or abs(actual-expected)>.1:raise ValueError(f'视频时长不符合任务：预计 {expected:.3f}，实际 {actual:.3f}')
    if bool(audio)!=bool(config.get('music_id')) or any(s.get('codec_name')!='aac' for s in audio):raise ValueError('音轨与音乐设置不一致')
    if audio and abs(float(audio[0].get('duration',0))-actual)>.15:raise ValueError('音轨时长与视频不一致')
    VideoEngine()._run([find_ffmpeg(),'-v','error','-xerror','-err_detect','explode','-i',str(path),'-map','0','-f','null','-'])
    return {'status':'passed','full_decode':True,'width':config['width'],'height':config['height'],'ratio':config['ratio'],'resolution':config['resolution'],'fit':config['fit'],'fps':30,'codec':'h264','duration':actual,'expected_duration':expected,'audio':bool(audio)}
