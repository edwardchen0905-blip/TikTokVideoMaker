import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error
import http.cookiejar
from core.workspace import Workspace
from app.desktop import DesktopService

class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.w=Workspace(self.root/'workspace')
        import subprocess
        self.files=[]
        for n,color in enumerate(['red','blue']):
            p=self.root/f'原始素材 {n}.png'
            subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'color={color}:s=80x80','-frames:v','1',str(p)],check=True,capture_output=True)
            self.files.append(p)
        result=self.w.import_assets(self.files)
        self.assertFalse(result['errors'],msg=str(result['errors']))
        self.ids=result['ids']
    def tearDown(self):
        self.w.stopping.set()
        if self.w.worker:self.w.worker.join(timeout=60)
        self.tmp.cleanup()
    def test_original_and_deduplication(self):
        before=[p.read_bytes() for p in self.files]
        self.assertEqual(self.w.import_assets(self.files)['ids'],self.ids)
        self.w.cleanup()
        self.assertEqual(before,[p.read_bytes() for p in self.files])
        self.assertEqual(len(self.w.snapshot()['assets']),2)
    def test_rule_validation_persistence_and_waiting(self):
        for indices in [[],[0],[-1],[True],[1.5]]:
            with self.assertRaises(ValueError):self.w.save_download_rule({'name':'invalid','main':'custom','indices':indices})
        rid=self.w.save_download_rule({'name':'手机壳视频素材规则','main':'custom','indices':[5,1,3,1],'details':True,'sku_images':True})
        self.w.save_sync_request({'store':'测试店铺','rule_id':rid,'scope':{'mode':'selected','ids':['CA001']}})
        self.w.save_download_rule({'id':rid,'name':'手机壳视频素材规则','main':'all'})
        state=Workspace(self.w.root).snapshot()
        self.assertEqual(state['sync_tasks'][0]['status'],'waiting_connection')
        self.assertEqual(json.loads(state['sync_tasks'][0]['rule_snapshot'])['indices'],[1,3,5])
        self.assertFalse(state['products'])
    def test_atomic_batch_and_separate_products(self):
        for n in range(2):
            pid=f'CA{n}';self.w.product({'id':pid,'title':pid});self.w.link_assets(pid,[self.ids[n]])
        with patch.object(self.w,'start_worker'):
            with self.assertRaises(ValueError):self.w.create_batch({'objects':[{'product_id':'CA0'},{'product_id':'missing'}],'templates':['fast_show']})
            self.assertEqual(self.w.snapshot()['tasks'],[])
            self.w.create_batch({'objects':[{'product_id':'CA0'},{'product_id':'CA1'}],'templates':['fast_show','product_show']})
        tasks=self.w.snapshot()['tasks'];self.assertEqual(len(tasks),4)
        for t in tasks:self.assertEqual(json.loads(t['assets']),[self.ids[int(t['product_id'][-1])]])
    def test_sku_matching(self):
        self.w.product({'id':'P','title':'Product','skus':['black','white']});skus=self.w.snapshot()['skus']
        self.w.link_assets('P',[self.ids[0]],{self.ids[0]:{'image_type':'main','position':1}})
        self.w.link_assets('P',[self.ids[1]],{self.ids[1]:{'image_type':'sku','position':1,'sku_id':skus[1]['id']}})
        with patch.object(self.w,'start_worker'):
            self.w.create_batch({'objects':[{'product_id':'P','sku_id':skus[0]['id']},{'product_id':'P','sku_id':skus[1]['id']}],'templates':['fast_show']})
        tasks=self.w.snapshot()['tasks']
        self.assertEqual(json.loads(tasks[0]['assets']),[self.ids[0]])
        self.assertEqual(json.loads(tasks[1]['assets']),self.ids)
    def test_real_batch_restart_and_publication(self):
        for n in range(2):
            pid=f'CA{n}';self.w.product({'id':pid,'title':pid});self.w.link_assets(pid,[self.ids[n]])
        self.w.create_batch({'objects':[{'product_id':'CA0'},{'product_id':'CA1'}],'templates':['fast_show','product_show']})
        worker=self.w.worker
        if worker:worker.join(timeout=90)
        state=self.w.snapshot()
        self.assertEqual([t['status'] for t in state['tasks']],['done']*4,msg=str(state['tasks']))
        self.assertEqual(len(state['videos']),4)
        v=state['videos'][0];self.w.queue_publication(v['id']);self.w.queue_publication(v['id'])
        self.assertEqual(len(self.w.snapshot()['publications']),1)
        with self.assertRaises(RuntimeError):self.w.publish('any')
        state=Workspace(self.w.root).snapshot()
        self.assertEqual(state['publications'][0]['status'],'pending');self.assertEqual(len(state['videos']),4)
        self.w.trash(v['id']);self.assertTrue(self.w.video_path(v['id']).exists())
        self.w.trash(v['id'],restore=True);self.assertFalse(self.w.snapshot()['videos'][0]['trashed'])
    def test_service_access_and_media_range(self):
        service=DesktopService(self.w);service.start()
        try:
            base=service.url.split('/start')[0]
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(base+'/api/state')
            opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            opener.open(service.url).read()
            self.assertEqual(len(json.load(opener.open(base+'/api/state'))['assets']),2)
            req=urllib.request.Request(base+'/media/asset/'+self.ids[0],headers={'Range':'bytes=0-7'})
            response=opener.open(req);self.assertEqual(response.status,206);self.assertEqual(response.read(),b'\x89PNG\r\n\x1a\n')
            req=urllib.request.Request(base+'/api/product',data=json.dumps({'id':'API','title':'API Product'}).encode(),headers={'Content-Type':'application/json'})
            self.assertEqual(json.load(opener.open(req))['id'],'API')
        finally:service.close()

    def test_hundred_products_two_templates(self):
        objects=[]
        for n in range(100):
            pid=f'BATCH-{n:03d}'
            self.w.product({'id':pid,'title':pid})
            self.w.link_assets(pid,[self.ids[n%2]])
            objects.append({'product_id':pid})
        with patch.object(self.w,'start_worker'):
            result=self.w.create_batch({'objects':objects,'templates':['fast_show','product_show']})
        self.assertEqual(result['tasks'],200)
        jobs=self.w.snapshot()['tasks']
        self.assertEqual(len({(t['product_id'],t['template']) for t in jobs}),200)

    def test_video_and_music_import_render(self):
        import subprocess
        from renderer.video_engine import probe_video
        video=self.root/'video.mp4';music=self.root/'music.wav'
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=green:s=80x80:d=0.5','-c:v','libx264',str(video)],check=True)
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=frequency=440:duration=0.5',str(music)],check=True)
        imported=self.w.import_assets([video,music])
        self.assertFalse(imported['errors'])
        self.w.create_batch({'objects':[{'assets':[self.ids[0],imported['ids'][0]]}],'templates':['fast_show'],'music':'selected','music_id':imported['ids'][1]})
        worker=self.w.worker
        if worker:worker.join(timeout=60)
        state=self.w.snapshot()
        self.assertEqual(state['tasks'][0]['status'],'done',state['tasks'][0]['error'])
        streams=probe_video(self.w.video_path(state['videos'][0]['id']))['streams']
        self.assertTrue(any(s['codec_type']=='audio' and s['codec_name']=='aac' for s in streams))
        self.assertTrue(any(s.get('width')==1080 and s.get('height')==1920 for s in streams))

if __name__=='__main__':unittest.main()
