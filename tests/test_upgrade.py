"""Real media and persistence regressions for the approved upgrade; no external success claims."""
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from core.workspace import Workspace
from renderer.video_engine import VideoEngine, validate_output, probe_video

ROOT=Path(__file__).resolve().parents[1]

class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="tvm_中文 O'Brien ")
        self.root=Path(self.tmp.name)
        self.w=Workspace(self.root/'data')
        originals=self.root/'originals';originals.mkdir()
        self.originals=[]
        for name in ('product_01.png','product_02.png'):
            p=originals/name;shutil.copy2(ROOT/'examples/input'/name,p);self.originals.append(p)
        self.ids=self.w.import_assets(self.originals)['ids']
        self.output=self.root/'自选 输出'

    def tearDown(self):
        self.w.stopping.set()
        if self.w.worker:self.w.worker.join(60)
        self.tmp.cleanup()

    def music(self):
        ids=[]
        for frequency in (440,880):
            p=self.root/f'tone {frequency}.wav'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'sine=frequency={frequency}:duration=0.3','-metadata',f'title=Tone {frequency}',str(p)],check=True,capture_output=True)
            ids+=self.w.import_assets([p])['ids']
        return ids

    def payload(self, **kw):
        return dict({'objects':[{'assets':self.ids}],'templates':['fast_show'],'image_duration':.5,'overrides':{'transition':'none','motion':'none'},'output_dir':str(self.output)},**kw)

    def finish(self):
        worker=self.w.worker
        if worker:worker.join(90)
        state=self.w.snapshot()
        self.assertFalse(any(t['status'] in ('waiting','running') for t in state['tasks']))
        self.assertTrue(all(t['status']=='done' for t in state['tasks']),str([(t['status'],t['error']) for t in state['tasks']]))
        return state

    def test_external_output_music_regeneration_and_restart(self):
        music=self.music()
        self.w.create_batch(self.payload(copies=2,music='multi',music_ids=music))
        state=self.finish();self.assertEqual(len(state['videos']),2)
        for v in state['videos']:
            task=next(t for t in state['tasks'] if t['id']==v['task_id']);cfg=json.loads(task['config'])
            self.assertTrue(Path(v['path']).parent.samefile(self.output))
            self.assertTrue(json.loads(v['validation'])['full_decode'])
            # Confirm the actual encoded soundtrack matches the assigned track, not just a DB id.
            raw=subprocess.run(['ffmpeg','-v','error','-i',v['path'],'-t','0.2','-ar','8000','-ac','1','-f','f32le','-'],check=True,capture_output=True).stdout
            samples=struct.unpack('<'+'f'*(len(raw)//4),raw)
            energy=lambda f:abs(sum(x*complex(math.cos(2*math.pi*f*i/8000),math.sin(2*math.pi*f*i/8000)) for i,x in enumerate(samples)))
            frequency=440 if cfg['music_id']==music[0] else 880
            self.assertGreater(energy(frequency),energy(880 if frequency==440 else 440)*5)
        old=state['videos'][0];old_cfg=next(t['config'] for t in state['tasks'] if t['id']==old['task_id'])
        self.w.save_settings({'output_dir':str(self.root/'different'),'music':'ai'})
        new=self.w.regenerate(old['id']);state=self.finish()
        self.assertEqual(next(t['config'] for t in state['tasks'] if t['id']==new),old_cfg)
        self.assertTrue(Path(next(v['path'] for v in state['videos'] if v['task_id']==new)).parent.samefile(self.output))
        reopened=Workspace(self.w.root).snapshot()
        self.assertEqual(len(reopened['videos']),3)
        self.assertEqual(reopened['videos'][0]['task_id'],new)

    def test_products_collection_snapshots_and_atomicity(self):
        for n,aid in enumerate(self.ids):
            self.w.product({'id':f'P{n}','title':f'Product {n}'})
            self.w.link_assets(f'P{n}',[aid])
        with patch.object(self.w,'start_worker'):
            separate=self.w.create_batch(self.payload(objects=[{'product_id':'P0'},{'product_id':'P1'}],templates=['fast_show','product_show']))
            self.assertEqual(separate['tasks'],4)
            for t in separate['preview']:self.assertEqual(t['assets'],[self.ids[int(t['product_id'][-1])]])
            group=self.w.create_batch(self.payload(objects=[{'product_ids':['P0','P1']}]))
            cfg=group['preview'][0]['config']
            self.assertEqual({r['product_id'] for r in cfg['source_relations']},{'P0','P1'})
            self.assertEqual(cfg['publication_content']['association'],'none')
            before=len(self.w.snapshot()['tasks'])
            with self.assertRaises(ValueError):self.w.create_batch(self.payload(objects=[{'product_id':'P0'},{'product_id':'MISSING'}]))
            self.assertEqual(before,len(self.w.snapshot()['tasks']))
        self.w.start_worker();self.finish()

    def test_custom_template_timing_transitions_and_motion(self):
        tid=self.w.save_template({'name':'用户配置','duration_per_image':2,'transition':'fade','transition_duration':.1,'motion':'none'})
        for transition,motion in [('none','none'),('fade','zoom'),('slideright','pan'),('slideup','none'),('slidedown','none'),('slideleft','none')]:
            self.w.create_batch(self.payload(templates=[tid],asset_durations={self.ids[0]:.4,self.ids[1]:.6},overrides={'transition':transition,'transition_duration':.1,'motion':motion}))
            self.finish()
        state=self.w.snapshot();self.assertEqual(len(state['videos']),6)
        for task in state['tasks']:
            cfg=json.loads(task['config']);self.assertEqual(cfg['durations'],[.4,.6]);self.assertEqual(cfg['duration_per_image'],2)
        with self.assertRaisesRegex(ValueError,'冲突'):self.w.create_batch(self.payload(timing='total',total_duration=2))
        p=self.payload(timing='total',total_duration=1.1);p.pop('image_duration')
        r=self.w.create_batch(p,preview=True);self.assertAlmostEqual(r['preview'][0]['config']['expected_duration'],1.1)

    def test_song_pool_order_random_and_ai_waiting(self):
        songs=self.music()
        p=self.payload(copies=7,music='multi',music_ids=songs)
        r=self.w.create_batch(p,preview=True)
        self.assertEqual([t['config']['music_id'] for t in r['preview']],(songs*4)[:7])
        p['music_order']='random';r=self.w.create_batch(p,preview=True)
        picks=[t['config']['music_id'] for t in r['preview']]
        self.assertTrue(all(a!=b for a,b in zip(picks,picks[1:])))
        for mode in ('ai','auto'):
            with self.assertRaisesRegex(ValueError,'等待接入'):self.w.create_batch(self.payload(music=mode))
        self.assertFalse(self.w.snapshot()['tasks'])

    def test_safe_delete_history_and_missing_regeneration(self):
        song=self.music()[0]
        with patch.object(self.w,'start_worker'):
            self.w.create_batch(self.payload(music='single',music_ids=[song]))
        with self.assertRaisesRegex(ValueError,'不能删除'):self.w.delete_asset(song)
        self.w.start_worker();state=self.finish();v=state['videos'][0]
        self.w.delete_asset(song)
        self.assertTrue((self.root/'tone 440.wav').is_file())
        saved=json.loads(self.w.snapshot()['tasks'][0]['config']);self.assertEqual(saved['music_snapshot']['Music_ID'],song)
        with self.assertRaisesRegex(ValueError,'缺失'):self.w.regenerate(v['id'])
        self.assertTrue(self.w.video_path(v['id']).is_file())

    def test_integrity_and_output_collision_never_delete_user_file(self):
        with patch.object(self.w,'start_worker'):
            r=self.w.create_batch(self.payload())
        output=self.output/(r['preview'][0]['id']+'.mp4');output.write_bytes(b'user existing file')
        self.w.start_worker()
        if self.w.worker:self.w.worker.join(60)
        self.assertEqual(output.read_bytes(),b'user existing file')
        self.assertEqual(self.w.snapshot()['tasks'][0]['status'],'failed')
        self.assertFalse(self.w.snapshot()['videos'])

    def test_content_and_real_waiting_queue(self):
        self.w.create_batch(self.payload());v=self.finish()['videos'][0]
        content={'caption':'真实说明','tags':'#商品','opening':'开头','cta':'查看商品','cover':'封面','association':'none'}
        self.w.save_content(v['id'],content)
        self.w.queue_publication(v['id'],'account A');self.w.queue_publication(v['id'],'account B')
        state=Workspace(self.w.root).snapshot();self.assertEqual(len(state['publications']),2)
        self.assertTrue(all(p['status']=='pending' and json.loads(p['content'])['caption']=='真实说明' for p in state['publications']))
        self.w.save_settings({'output_retention':'after_uploaded'})
        self.w.apply_retention();self.assertTrue(self.w.video_path(v['id']).exists())
        with self.assertRaisesRegex(RuntimeError,'尚未执行'):self.w.publish(state['publications'][0]['id'])

    def test_cleanup_saved_days_and_original_protection(self):
        old=self.w.root/'cache'/'old';old.mkdir();(old/'temp').write_text('old')
        new=self.w.root/'cache'/'new';new.mkdir();(new/'temp').write_text('new')
        import os
        os.utime(old,(time.time()-10*86400,)*2)
        self.w.save_settings({'cacheDays':'7'});self.w.apply_retention()
        self.assertFalse(old.exists());self.assertTrue(new.exists())
        self.assertTrue(all(p.is_file() for p in self.originals))
        self.assertEqual(self.w.save_settings({'cacheDays':'0'})['cacheDays'],'0')
        self.w.apply_retention();self.assertTrue(new.exists())

    def test_decode_checks_reject_wrong_settings_and_corruption(self):
        r=self.w.create_batch(self.payload());v=self.finish()['videos'][0];cfg=r['preview'][0]['config']
        with self.assertRaises(ValueError):validate_output(v['path'],dict(cfg,expected_duration=10))
        with self.assertRaises(ValueError):validate_output(v['path'],dict(cfg,music_id='missing'))
        bad=self.root/'corrupt.mp4';bad.write_bytes(Path(v['path']).read_bytes()[:100])
        with self.assertRaises((ValueError,KeyError)):validate_output(bad,cfg)

    def test_extreme_images_keep_all_four_corners_during_motion(self):
        # Four colored corner markers must survive every sampled frame, including motion endpoints.
        colors=[(255,0,0),(0,255,0),(0,0,255),(255,255,0)]
        for width,height in ((2400,120),(120,2400)):
            pixels=bytearray([80])*(width*height*3)
            for i,color in enumerate(colors):
                for y in range((height*3//4 if i//2 else 0),(height if i//2 else height//4)):
                    for x in range((width*3//4 if i%2 else 0),(width if i%2 else width//4)):
                        offset=(y*width+x)*3;pixels[offset:offset+3]=bytes(color)
            source=self.root/f'corners-{width}.ppm'
            source.write_bytes(f'P6\n{width} {height}\n255\n'.encode()+pixels)
            for motion in ('none','zoom','pan'):
                output=self.root/f'corners-{width}-{motion}.mp4'
                VideoEngine().generate([source],{'duration_per_image':.5,'transition':'none','transition_duration':0,'motion':motion},output)
                frames=subprocess.run(['ffmpeg','-v','error','-i',str(output),'-vf','scale=270:480','-pix_fmt','rgb24','-f','rawvideo','-'],check=True,capture_output=True).stdout
                size=270*480*3
                for frame in (frames[:size],frames[7*size:8*size],frames[-size:]):
                    found=[0]*4
                    for j in range(0,len(frame),3):
                        r,g,b=frame[j:j+3]
                        for i,(cr,cg,cb) in enumerate(colors):
                            if abs(r-cr)<70 and abs(g-cg)<70 and abs(b-cb)<70:found[i]+=1
                    self.assertTrue(all(n>=4 for n in found),(width,motion,found))

if __name__=='__main__':unittest.main()
