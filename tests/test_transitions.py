"""Actual FFmpeg regressions for effects, mixed cuts, canvas sizes and image protection."""
import math
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import wave

from core.render_config import RESOLUTIONS, TRANSITIONS, transition_plan, validate_config
from renderer.video_engine import VideoEngine, find_ffmpeg, probe_video, validate_output


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tvm_effects_')
        self.root = Path(self.temp.name)
        self.engine = VideoEngine()
        self.gray = self.root/'gray.ppm'
        self.markers = self.root/'markers.ppm'
        self.gray.write_bytes(b'P6\n320 320\n255\n'+bytes([128])*320*320*3)
        pixels = bytearray([225])*320*320*3
        for i, color in enumerate(((255,0,0), (0,255,0), (0,0,255), (255,255,0))):
            for y in range(256 if i//2 else 0, 320 if i//2 else 64):
                for x in range(256 if i%2 else 0, 320 if i%2 else 64):
                    offset = (y*320+x)*3
                    pixels[offset:offset+3] = bytes(color)
        self.markers.write_bytes(b'P6\n320 320\n255\n'+pixels)

    def tearDown(self):
        self.temp.cleanup()

    def config(self, count=2, **changes):
        cfg = validate_config(dict(resolution='1080x1080', duration_per_image=.4, transition_duration=.2, **changes))
        sequence, overlaps = transition_plan(cfg, count)
        cfg.update(transition_sequence=sequence, transition_durations=overlaps,
                   durations=[cfg['duration_per_image']]*count,
                   expected_duration=count*cfg['duration_per_image']-sum(overlaps))
        return cfg

    def frame(self, output, seconds, width=120, height=120):
        return subprocess.run([find_ffmpeg(), '-v', 'error', '-ss', str(seconds), '-i', str(output),
            '-frames:v', '1', '-vf', f'scale={width}:{height}', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
            check=True, capture_output=True).stdout

    def test_every_effect_encodes_and_safe_zoom_keeps_corner_markers(self):
        for effect in TRANSITIONS:
            with self.subTest(effect=effect['id']):
                cfg = self.config(transitions=[effect['id']])
                output = self.root/(effect['id']+'.mp4')
                self.engine.generate([self.gray, self.markers], cfg, output)
                checked = validate_output(output, cfg)
                self.assertTrue(checked['full_decode'])
                self.assertEqual(checked['resolution'], '1080x1080')
                self.assertAlmostEqual(checked['duration'], cfg['expected_duration'], places=2)
                if effect['id'] == 'zoomsafe':
                    # Halfway through the transition, all incoming corner markers
                    # must exist inside the canvas, including their outer edges.
                    frame = self.frame(output, .3)
                    colors = []
                    for x,y in ((12,12), (107,12), (12,107), (107,107)):
                        offset = (y*120+x)*3
                        colors.append(tuple(frame[offset:offset+3]))
                    r,g,b,y = colors
                    self.assertGreater(r[0]-max(r[1:]), 40, colors)
                    self.assertGreater(g[1]-max(g[0],g[2]), 40, colors)
                    self.assertGreater(b[2]-max(b[:2]), 40, colors)
                    self.assertGreater(min(y[:2])-y[2], 40, colors)

    def test_mixed_direct_cuts_cycle_and_audio_have_exact_total(self):
        cfg = validate_config({'transitions':['none','fade'], 'transition_duration':.1,
                               'duration_per_image':.3, 'resolution':'1080x1080'})
        sequence, overlaps = transition_plan(cfg, 5)
        self.assertEqual(sequence, ['none','fade','none','fade'])
        self.assertEqual(overlaps, [0,.1,0,.1])
        cfg.update(durations=[.3]*5, transition_sequence=sequence, transition_durations=overlaps,
                   expected_duration=1.3, music_id='recorded-music', music_playback='loop')
        music = self.root/'music.wav'
        with wave.open(str(music), 'wb') as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(8000)
            stream.writeframes(b''.join(struct.pack('<h', round(5000*math.sin(2*math.pi*440*i/8000))) for i in range(2400)))
        output = self.root/'mixed.mp4'
        self.engine.generate([self.markers,self.gray,self.markers,self.gray,self.markers], cfg, output, music)
        checked = validate_output(output, cfg)
        self.assertTrue(checked['audio'])
        self.assertAlmostEqual(checked['duration'], 1.3, places=2)
        audio = next(s for s in probe_video(output)['streams'] if s['codec_type']=='audio')
        self.assertLess(abs(float(audio['duration'])-1.3), .1)

    def test_three_sizes_keep_complete_square_without_stretch(self):
        for resolution,motion in zip(RESOLUTIONS, ('zoom','none','pan')):
            with self.subTest(resolution=resolution['id'], motion=motion):
                cfg = validate_config({'resolution':resolution['id'], 'motion':motion,
                                       'duration_per_image':.4, 'transitions':['none']})
                cfg['expected_duration'] = .4
                output = self.root/(resolution['id']+'.mp4')
                self.engine.generate([self.markers], cfg, output)
                checked = validate_output(output, cfg)
                self.assertEqual((checked['width'],checked['height']), (resolution['width'],resolution['height']))
                self.assertEqual(checked['fit'], 'contain')
                width,height = (90,160) if resolution['ratio']=='9:16' else ((120,120) if resolution['ratio']=='1:1' else (160,90))
                for time in (0,.2,11/30):
                    frame = self.frame(output, time, width, height)
                    groups = [[] for _ in range(4)]
                    for offset in range(0,len(frame),3):
                        r,g,b = frame[offset:offset+3]
                        for i,color in enumerate(((255,0,0),(0,255,0),(0,0,255),(255,255,0))):
                            if all(abs(a-c)<65 for a,c in zip((r,g,b),color)):
                                pixel = offset//3;groups[i].append((pixel%width,pixel//width))
                    self.assertTrue(all(len(group)>=4 for group in groups), (resolution,motion,time,[len(g) for g in groups]))
                    points = [point for group in groups for point in group]
                    span_x = max(p[0] for p in points)-min(p[0] for p in points)
                    span_y = max(p[1] for p in points)-min(p[1] for p in points)
                    self.assertLessEqual(abs(span_x-span_y), 2, (resolution,motion,time,span_x,span_y))

    def test_legacy_config_and_saved_plan_validation(self):
        legacy = validate_config({'duration_per_image':.4, 'transition':'slideleft', 'transition_duration':.1, 'motion':'none'})
        self.assertEqual(transition_plan(legacy, 3), (['slideleft','slideleft'], [.1,.1]))
        self.assertEqual(legacy['resolution'], '1080x1920')
        for values in ({'transitions':[]}, {'transitions':['missing']}, {'fit':'crop'}, {'resolution':'wrong'}, {'music_volume':float('nan')}, {'expected_duration':float('nan')}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_config(values)
        cfg = self.config(transitions=['fade'])
        cfg['transition_sequence'] = ['none']
        with self.assertRaisesRegex(ValueError, '不一致'):
            self.engine.generate([self.gray,self.markers], cfg, self.root/'invalid.mp4')


if __name__ == '__main__':
    unittest.main()
