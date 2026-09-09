"""Synthetic evidence fixtures; never installed as the user's product or music library."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from core.workspace import Workspace

ROOT=Path(__file__).resolve().parents[1]
class LocalContentTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.w=Workspace(Path(self.tmp.name)/'data')
  self.a=self.w.import_assets([ROOT/'examples/input/product_01.png'])['ids']
 def tearDown(self):
  self.w.stopping.set()
  if self.w.worker:self.w.worker.join(60)
  self.tmp.cleanup()
 def keyword(self,kind,key,en,th='คำทดสอบ',**extra):
  r=dict(kind=kind,key=key,en=en,th=th,source='测试资料，不随软件分发',confirmed=True,**extra)
  return self.w.save_local_record(r)['id']
 def data(self):
  p=self.keyword('pattern','星星','stars',tags=['gentle'])
  m=self.keyword('material','硅胶','silicone',th='ซิลิโคน')
  c=self.keyword('color','蓝','blue',th='สีน้ำเงิน',aliases=['蓝色'])
  body={k:'{keywords}' for k in ('title','caption','tags','opening','cta','cover')}
  text=self.w.save_local_record(dict(kind='text',key='针对性测试',language='en',body=body,source='test',generic=False))['id']
  self.w.product(dict(id='P1',title='商品',skus=['星星-硅胶-蓝']))
  self.w.link_assets('P1',self.a)
  return p,m,c,text
 def payload(self,**extra):
  return dict(objects=[{'assets':self.a}],templates=['fast_show'],music='none',image_duration=.4,overrides={'transition':'none','motion':'none'},local_copy={'mode':'local','language':'en','sources':[]},**extra)
 def preview(self,p):return self.w.create_batch(p,preview=True)['preview'][0]['config']
 def test_management_import_identity_restart_and_deleted_seed(self):
  self.assertEqual(len(self.w.local_records()),6)
  r={'kind':'pattern','key':'test','en':'stars','source':'fixture','confirmed':True}
  result=self.w.import_local_records([r,r,{'kind':'invalid'}]);self.assertEqual(len(result['imported']),1);self.assertEqual(len(result['errors']),2)
  saved=next(x for x in self.w.local_records() if x['key']=='test')
  self.w.save_local_record(dict(saved,en='dots'))
  with self.assertRaises(ValueError):self.w.save_local_record(saved)
  current=next(x for x in Workspace(self.w.root).local_records() if x['key']=='test');self.assertEqual(current['en'],'dots')
  self.w.delete_local_record(current['id'],current['version'])
  built=self.w.local_records()[0];self.w.delete_local_record(built['id'],built['version'])
  self.assertNotIn(built['id'],{r['id'] for r in Workspace(self.w.root).local_records()})
 def test_sku_all_seven_combinations_and_unselected_sources(self):
  self.data()
  for mask in range(1,8):
   sources=[s for i,s in enumerate(['sku_pattern','sku_material','sku_color']) if mask&(1<<i)]
   p=self.payload();p['objects']=[{'product_id':'P1'}];p['local_copy']['sources']=sources
   cfg=self.preview(p);content=cfg['publication_content']['caption']
   for i,word in enumerate(['stars','silicone','blue']):self.assertEqual(word in content,bool(mask&(1<<i)))
   self.assertFalse(cfg['text_trace']['fallback']['caption'])
   self.assertEqual(cfg['text_trace']['sku'][0]['raw'],'星星-硅胶-蓝')
  self.w.product({'id':'P2','title':'商品','skus':['星星-额外-硅胶-蓝']});self.w.link_assets('P2',self.a)
  p['objects']=[{'product_id':'P2'}];cfg=self.preview(p)
  self.assertTrue(cfg['text_trace']['unmatched']);self.assertTrue(cfg['text_trace']['fallback']['caption'])
 def test_language_fallback_missing_library_and_no_placeholder(self):
  p=self.payload();en=self.preview(p);p['local_copy']['language']='th';th=self.preview(p)
  self.assertNotEqual(en['publication_content']['caption'],th['publication_content']['caption'])
  self.assertTrue(all(th['text_trace']['fallback'].values()))
  for r in self.w.local_records():
   if r['language']=='th':self.w.delete_local_record(r['id'],r['version'])
  with self.assertRaisesRegex(ValueError,'没有可用通用文本'):self.preview(p)
  self.assertFalse(self.w.snapshot()['tasks'])
 def test_negative_conflicting_facts_and_collection(self):
  self.data();self.keyword('keyword','磁吸','magnetic',property='feature',aliases=['磁力吸附'])
  p=self.payload();p['objects']=[{'product_id':'P1'}];p['local_copy']['sources']=['product_title','product_details']
  self.w.product({'id':'P1','title':'支持磁吸','details':'不支持磁吸'})
  self.assertNotIn('magnetic',self.preview(p)['publication_content']['caption'])
  self.w.product({'id':'P1','title':'支持磁吸','details':''})
  self.assertIn('magnetic',self.preview(p)['publication_content']['caption'])
  p['local_copy']['sources']=[];self.assertNotIn('magnetic',self.preview(p)['publication_content']['caption'])
  self.keyword('color','红','red',th='สีแดง');self.w.product({'id':'P2','title':'商品','skus':['星星-硅胶-红']});self.w.link_assets('P2',self.a)
  p['objects']=[{'product_ids':['P1','P2']}];p['local_copy']['sources']=['sku_pattern','sku_material','sku_color']
  caption=self.preview(p)['publication_content']['caption'];self.assertIn('stars',caption);self.assertNotIn('blue',caption);self.assertNotIn('red',caption)
 def test_video_titles_never_supply_feature_evidence(self):
  self.data();self.keyword('keyword','磁吸','magnetic',property='feature')
  self.w.product({'id':'P1','title':'普通商品','video_titles':'磁吸'})
  p=self.payload();p['objects']=[{'product_id':'P1'}];p['local_copy']['sources']=['video_titles']
  cfg=self.preview(p);self.assertNotIn('magnetic',cfg['publication_content']['caption']);self.assertTrue(cfg['text_trace']['keywords'])
 def test_offline_music_real_output_and_history_snapshots(self):
  pid,_,_,_=self.data();music=self.w.import_assets([ROOT/'examples/input/sample_music.mp3'])['ids'][0]
  self.w.update_music(music,{'tags':'quiet'})
  self.w.save_local_record(dict(kind='music_rule',key='test matching',source='fixture',match_tags=['gentle'],music_tags=['quiet']))
  p=self.payload();p['local_copy']['sources']=['existing'];p['local_copy']['keyword_ids']=[pid];p['music']='local'
  with patch('socket.create_connection',side_effect=AssertionError('Network forbidden')),patch('urllib.request.urlopen',side_effect=AssertionError('Network forbidden')):
   result=self.w.create_batch(p)
   self.w.worker.join(60)
   state=self.w.snapshot();self.assertEqual(state['tasks'][0]['status'],'done',state['tasks'][0]['error'])
  cfg=json.loads(state['tasks'][0]['config']);self.assertEqual(cfg['music_id'],music);self.assertEqual(cfg['music_match']['status'],'matched')
  self.assertTrue(json.loads(state['videos'][0]['validation'])['audio'])
  before=state['videos'][0]['content'];record=next(r for r in self.w.local_records() if r['id']==pid)
  self.w.save_local_record(dict(record,en='modified'));self.w.delete_local_record(pid,record['version']+1)
  new=self.w.regenerate(state['videos'][0]['id']);self.w.worker.join(60)
  after=self.w.snapshot();self.assertEqual(after['videos'][0]['content'],before)
  self.assertEqual(next(t['config'] for t in after['tasks'] if t['id']==new),state['tasks'][0]['config'])
  self.w.update_music(music,{'tags':''});p['local_copy']['keyword_ids']=[]
  self.assertEqual(self.preview(p)['music_match']['status'],'unmatched')
  with self.assertRaisesRegex(ValueError,'未找到音乐'):self.w.create_batch(p)
 def test_shuffle_and_selected_template_pool(self):
  p=self.payload();p['copies']=4;p['seed']=7;p['local_copy']['order']='random'
  result=self.w.create_batch(p,preview=True)['preview'];titles=[t['config']['publication_content']['title'] for t in result]
  self.assertEqual(len(set(titles[:2])),2);self.assertNotEqual(titles[1],titles[2])
  with self.assertRaises(ValueError):self.keyword('material','bad','waterproof silicone')

 def test_missing_translation_category_scope_and_explicit_pool(self):
  key=self.keyword('pattern','泰文限定','',th='ลวดลาย')
  p=self.payload();p['local_copy'].update(sources=['existing'],keyword_ids=[key])
  self.assertTrue(self.preview(p)['text_trace']['fallback']['caption'])
  body={k:'Only this category' for k in ('title','caption','tags','opening','cta','cover')}
  tid=self.w.save_local_record(dict(kind='text',key='specific category',language='en',generic=True,body=body,category='verified category',source='test'))['id']
  p['local_copy']['text_ids']=[tid]
  self.assertNotEqual(self.preview(p)['publication_content']['caption'],'Only this category')
  self.w.product({'id':'P1','title':'Product','category':'verified category'});self.w.link_assets('P1',self.a);p['objects']=[{'product_id':'P1'}]
  self.assertEqual(self.preview(p)['publication_content']['caption'],'Only this category')
  p['local_copy']['language']='zh'
  with self.assertRaises(ValueError):self.preview(p)

if __name__=='__main__':unittest.main()
