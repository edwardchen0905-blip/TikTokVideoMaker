"""The single persistent product, asset, production and publication workspace."""
from __future__ import annotations
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
import uuid
import random
import tempfile
from datetime import datetime
from core.template_manager import TemplateManager, seconds
from core import local_content

KINDS = {'.png':'image','.jpg':'image','.jpeg':'image','.webp':'image','.mp4':'video','.mov':'video','.mkv':'video','.webm':'video','.mp3':'music','.wav':'music','.m4a':'music','.aac':'music','.flac':'music'}

def uid(): return uuid.uuid4().hex

def now(): return datetime.now().isoformat(timespec='microseconds')

class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve()
        for name in ('original_imports','downloads','cache','output'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connection() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS products(id TEXT PRIMARY KEY,title TEXT NOT NULL,store TEXT NOT NULL DEFAULT '',url TEXT NOT NULL DEFAULT '',created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS skus(id TEXT PRIMARY KEY,product_id TEXT NOT NULL REFERENCES products(id),name TEXT NOT NULL,UNIQUE(product_id,name));
            CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,source TEXT NOT NULL,original_path TEXT NOT NULL,path TEXT NOT NULL,hash TEXT NOT NULL UNIQUE,size INTEGER NOT NULL,created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS product_assets(product_id TEXT NOT NULL REFERENCES products(id),asset_id TEXT NOT NULL REFERENCES assets(id),position INTEGER NOT NULL,PRIMARY KEY(product_id,asset_id));
            CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY,name TEXT NOT NULL,created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES batches(id),product_id TEXT REFERENCES products(id),sku_id TEXT REFERENCES skus(id),assets TEXT NOT NULL,template TEXT NOT NULL,config TEXT NOT NULL,status TEXT NOT NULL,progress INTEGER NOT NULL DEFAULT 0,output TEXT,error TEXT NOT NULL DEFAULT '',created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS videos(id TEXT PRIMARY KEY,task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),path TEXT NOT NULL,duration REAL NOT NULL,created TEXT NOT NULL,trashed INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS publications(id TEXT PRIMARY KEY,video_id TEXT NOT NULL REFERENCES videos(id),target TEXT NOT NULL DEFAULT '',status TEXT NOT NULL CHECK(status IN ('pending','uploading','uploaded','published','failed')),error TEXT NOT NULL DEFAULT '',created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sync_tasks(id TEXT PRIMARY KEY,store TEXT NOT NULL,status TEXT NOT NULL,error TEXT NOT NULL DEFAULT '',created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS download_rules(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,config TEXT NOT NULL,updated TEXT NOT NULL);
            ''')
            columns={r['name'] for r in db.execute('PRAGMA table_info(product_assets)')}
            if 'image_type' not in columns:
                db.executescript('''
                CREATE TABLE product_assets_new(id INTEGER PRIMARY KEY,product_id TEXT NOT NULL REFERENCES products(id),asset_id TEXT NOT NULL REFERENCES assets(id),position INTEGER NOT NULL,image_type TEXT NOT NULL DEFAULT 'local',sku_id TEXT REFERENCES skus(id));
                INSERT INTO product_assets_new(product_id,asset_id,position) SELECT product_id,asset_id,position FROM product_assets;
                DROP TABLE product_assets;
                ALTER TABLE product_assets_new RENAME TO product_assets;
                CREATE UNIQUE INDEX asset_relation_identity ON product_assets(product_id,asset_id,image_type,position,COALESCE(sku_id,''));
                ''')
            sync_columns={r['name'] for r in db.execute('PRAGMA table_info(sync_tasks)')}
            for name in ('rule_id','rule_snapshot','product_scope'):
                if name not in sync_columns:db.execute(f'ALTER TABLE sync_tasks ADD COLUMN {name} TEXT')
            if 'source_id' not in {r['name'] for r in db.execute('PRAGMA table_info(skus)')}:
                db.execute('ALTER TABLE skus ADD COLUMN source_id TEXT')
            db.execute("UPDATE tasks SET status='failed',error='软件在任务完成前关闭，可重新执行',progress=0 WHERE status='running'")
            for table, fields in {
                'assets': {'metadata': "TEXT NOT NULL DEFAULT '{}'", 'deleted': 'INTEGER NOT NULL DEFAULT 0'},
                'videos': {'validation': "TEXT NOT NULL DEFAULT '{}'", 'content': "TEXT NOT NULL DEFAULT '{}'", 'deleted': 'INTEGER NOT NULL DEFAULT 0'},
            }.items():
                existing={r['name'] for r in db.execute('PRAGMA table_info('+table+')')}
                for name, declaration in fields.items():
                    if name not in existing:db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}')
            if 'content' not in {r['name'] for r in db.execute('PRAGMA table_info(publications)')}:
                db.execute("ALTER TABLE publications ADD COLUMN content TEXT NOT NULL DEFAULT '{}'")
            db.execute('CREATE TABLE IF NOT EXISTS user_templates(id TEXT PRIMARY KEY,config TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS local_records(id TEXT PRIMARY KEY,kind TEXT NOT NULL,key TEXT NOT NULL,language TEXT NOT NULL,config TEXT NOT NULL,version INTEGER NOT NULL,deleted INTEGER NOT NULL DEFAULT 0,UNIQUE(kind,key,language))')
            for r in local_content.builtins():
                r=local_content.validate(r)
                db.execute('INSERT OR IGNORE INTO local_records(id,kind,key,language,config,version) VALUES(?,?,?,?,?,1)',(r['id'],r['kind'],local_content.norm(r['key']),r['language'],json.dumps(r)))
            if 'metadata' not in {r['name'] for r in db.execute('PRAGMA table_info(products)')}:
                db.execute("ALTER TABLE products ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
        self.worker = None
        self.stopping = threading.Event()

    @contextlib.contextmanager
    def connection(self):
        with self.lock:
            db=sqlite3.connect(self.root/'workspace.sqlite3',timeout=30)
            db.row_factory=sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            try:
                with db: yield db
            finally: db.close()

    def rows(self, sql, args=()):
        with self.connection() as db: return [dict(r) for r in db.execute(sql,args)]

    def save_settings(self, values):
        allowed={'source','template','duration','music','music_ids','music_order','cacheDays','density','output_dir','download_retention','output_retention','production'}
        if set(values)-allowed: raise ValueError('设置字段无效')
        if str(values.get('cacheDays','7')) not in {'0','1','7','30'}: raise ValueError('缓存保留时间无效')
        if values.get('music','local') not in {'none','local','ai','single','multi'}:raise ValueError('音乐模式无效；AI 选曲等待接入')
        if values.get('download_retention','keep') not in {'keep','after_tasks'}:raise ValueError('下载保留策略无效')
        if values.get('output_retention','keep') not in {'keep','after_uploaded','after_published'}:raise ValueError('输出保留策略无效')
        if values.get('output_dir'):values['output_dir']=str(self.check_output_dir(values['output_dir']))
        with self.connection() as db:
            stored=db.execute("SELECT value FROM settings WHERE key='production'").fetchone()
            production=dict(values.get('production',json.loads(stored[0]) if stored else {}))
            for key in ('music','music_ids','music_order','output_dir'):
                if key in values:production[key]=values[key]
            if production:values=dict(values,production=production)
            for k,v in values.items(): db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(k,json.dumps(v)))
        return self.snapshot()['settings']

    def product(self, p):
        pid=str(p.get('id','')).strip();title=str(p.get('title','')).strip()
        if not pid or not title: raise ValueError('商品 ID 和标题不能为空')
        if len(pid)>150 or len(title)>1000: raise ValueError('商品 ID 或标题过长')
        raw_skus=p.get('skus',[])
        if not isinstance(raw_skus,list):raise ValueError('SKU 必须为列表')
        skus=[]
        for s in raw_skus:
            item={'name':s} if isinstance(s,str) else s
            if not isinstance(item,dict):raise ValueError('SKU 格式无效')
            name=str(item.get('name','')).strip()
            if name:skus.append((name,str(item.get('source_id','')).strip() or None))
        with self.connection() as db:
            existing=db.execute('SELECT id FROM products WHERE id=?',(pid,)).fetchone()
            if existing:
                db.execute('UPDATE products SET title=?,store=?,url=? WHERE id=?',(title,str(p.get('store','')),str(p.get('url','')),pid))
            else: db.execute('INSERT INTO products(id,title,store,url,created) VALUES(?,?,?,?,?)',(pid,title,str(p.get('store','')),str(p.get('url','')),now()))
            old=json.loads(db.execute('SELECT metadata FROM products WHERE id=?',(pid,)).fetchone()[0])
            for field in ('details','category','keywords','video_titles'):
                if field in p:
                    if not isinstance(p[field],str) or len(p[field])>20000:raise ValueError('商品资料格式无效或过长')
                    old[field]=p[field]
            db.execute('UPDATE products SET metadata=? WHERE id=?',(json.dumps(old),pid))
            for name,source_id in dict.fromkeys(skus):
                db.execute('INSERT INTO skus(id,product_id,name,source_id) VALUES(?,?,?,?) ON CONFLICT(product_id,name) DO UPDATE SET source_id=COALESCE(excluded.source_id,skus.source_id)',(uid(),pid,name,source_id))
        return pid

    def import_assets(self, paths):
        from renderer.video_engine import probe_video
        imported=[];errors=[]
        candidates=[]
        for raw in paths:
            p=Path(raw).expanduser().resolve()
            candidates.extend(sorted(p.rglob('*')) if p.is_dir() else [p])
        for p in candidates:
            if not p.is_file() or p.suffix.lower() not in KINDS: continue
            if p.is_relative_to(self.root):
                errors.append({'name':p.name,'error':'不能重复导入软件自身的工作目录'});continue
            try:
                info=probe_video(p)
                kind=KINDS[p.suffix.lower()]
                expected='audio' if kind=='music' else 'video'
                if not any(s.get('codec_type')==expected for s in info.get('streams',[])): raise ValueError('素材损坏或无法读取')
                digest=hashlib.sha256()
                with p.open('rb') as f:
                    for block in iter(lambda:f.read(1024*1024),b''):digest.update(block)
                fingerprint=digest.hexdigest()
                with self.connection() as db:
                    old=db.execute('SELECT id FROM assets WHERE hash=?',(fingerprint,)).fetchone()
                    if old:
                        row=db.execute('SELECT * FROM assets WHERE id=?',(old['id'],)).fetchone()
                        dest=self.root/row['path']
                        if row['deleted'] or not dest.is_file():
                            dest.parent.mkdir(parents=True,exist_ok=True)
                            shutil.copy2(p,dest)
                            db.execute('UPDATE assets SET deleted=0 WHERE id=?',(old['id'],))
                        imported.append(old['id']);continue
                    aid=uid();dest=self.root/'original_imports'/aid/p.name;dest.parent.mkdir()
                    try:
                        shutil.copy2(p,dest)
                        tags={str(k).lower():v for k,v in info.get('format',{}).get('tags',{}).items()}
                        for stream in info.get('streams',[]):
                            tags.update({str(k).lower():v for k,v in stream.get('tags',{}).items()})
                        metadata={'title':tags.get('title',p.name),'author':tags.get('artist',''),'source_url':'','license':tags.get('copyright',''),'tags':tags.get('genre',''),'duration':float(info.get('format',{}).get('duration',0))}
                        db.execute('INSERT INTO assets(id,name,kind,source,original_path,path,hash,size,created,metadata) VALUES(?,?,?,?,?,?,?,?,?,?)',(aid,p.name,kind,'local',str(p),str(dest.relative_to(self.root)),fingerprint,p.stat().st_size,now(),json.dumps(metadata)))
                    except Exception:
                        shutil.rmtree(dest.parent);raise
                    imported.append(aid)
            except Exception as e:errors.append({'name':p.name,'error':str(e)})
        return {'ids':list(dict.fromkeys(imported)),'errors':errors}

    def link_assets(self,pid,ids,metadata=None):
        with self.connection() as db:
            if not db.execute('SELECT 1 FROM products WHERE id=?',(pid,)).fetchone(): raise ValueError('商品不存在')
            position=db.execute('SELECT COALESCE(MAX(position),-1)+1 FROM product_assets WHERE product_id=?',(pid,)).fetchone()[0]
            for i,aid in enumerate(dict.fromkeys(ids)):
                if not db.execute('SELECT 1 FROM assets WHERE id=?',(aid,)).fetchone(): raise ValueError('素材不存在')
                if metadata is not None:
                    info=metadata.get(aid,{})
                    kind=info.get('image_type','local');order=info.get('position',position+i);sku=info.get('sku_id') or None
                    if kind not in {'local','main','detail','sku','video'}:raise ValueError('图片类型无效')
                    if type(order) is not int or order<(0 if kind=='local' else 1):raise ValueError('图片排序无效')
                    if kind=='sku' and not sku:raise ValueError('SKU 图片必须关联 SKU ID')
                    if sku and not db.execute('SELECT 1 FROM skus WHERE id=? AND product_id=?',(sku,pid)).fetchone():raise ValueError('SKU 与商品不匹配')
                    db.execute('INSERT OR IGNORE INTO product_assets(product_id,asset_id,position,image_type,sku_id) VALUES(?,?,?,?,?)',(pid,aid,order,kind,sku))
                elif not db.execute("SELECT 1 FROM product_assets WHERE product_id=? AND asset_id=? AND image_type='local'",(pid,aid)).fetchone():
                    db.execute("INSERT INTO product_assets(product_id,asset_id,position,image_type) VALUES(?,?,?,'local')",(pid,aid,position+i))

    def save_download_rule(self,payload):
        name=str(payload.get('name','')).strip()
        if not name or len(name)>150:raise ValueError('请填写 1 到 150 字的规则名称')
        mode=payload.get('main','first')
        if mode not in {'first','all','custom'}:raise ValueError('主图选择方式无效')
        indices=payload.get('indices',[])
        if not isinstance(indices,list):raise ValueError('自定义主图序号必须为列表')
        if any(type(n) is not int or n<1 or n>1000 for n in indices):raise ValueError('主图序号必须为正整数，最大 1000')
        indices=sorted(set(indices)) if mode=='custom' else []
        if mode=='custom' and not indices:raise ValueError('请指定至少一张主图序号')
        for key in ('details','sku_images','product_video'):
            if key in payload and type(payload[key]) is not bool:raise ValueError('下载开关必须为布尔值')
        if payload.get('product_video',False):raise ValueError('商品视频下载当前未开放')
        config={'main':mode,'indices':indices,'details':payload.get('details',True),'sku_images':payload.get('sku_images',True),'product_video':False}
        rid=payload.get('id') or uid()
        with self.connection() as db:
            db.execute('INSERT INTO download_rules VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,config=excluded.config,updated=excluded.updated',(rid,name,json.dumps(config),now()))
        return rid

    def save_sync_request(self,payload):
        # Configuration only: no browser action, no network access, no success transition.
        store=str(payload.get('store','')).strip()
        if not store:raise ValueError('请填写待连接的店铺名称')
        scope=payload.get('scope',{'mode':'all','ids':[]})
        if not isinstance(scope,dict) or scope.get('mode') not in {'all','selected'}:raise ValueError('商品范围无效')
        ids=scope.get('ids',[])
        if not isinstance(ids,list) or any(not isinstance(x,str) or not x.strip() for x in ids):raise ValueError('商品 ID 列表无效')
        if scope['mode']=='selected' and not ids:raise ValueError('请填写指定商品 ID')
        with self.connection() as db:
            rule=db.execute('SELECT * FROM download_rules WHERE id=?',(payload.get('rule_id'),)).fetchone()
            if not rule:raise ValueError('请选择已保存的下载规则')
            tid=uid()
            db.execute('INSERT INTO sync_tasks(id,store,status,error,created,rule_id,rule_snapshot,product_scope) VALUES(?,?,?,?,?,?,?,?)',(tid,store,'waiting_connection','等待连接 TikTok 店铺环境',now(),rule['id'],rule['config'],json.dumps(scope)))
        return tid

    def check_output_dir(self, path=None):
        result=Path(path or self.root/'output').expanduser().resolve()
        if not result.is_absolute():raise ValueError('输出目录无效')
        if any(result.is_relative_to(self.root/n) for n in ('cache','original_imports','downloads','browser')):raise ValueError('输出目录不能放在素材或缓存目录内')
        result.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryFile(dir=result):pass
        return result

    def templates(self):
        manager=TemplateManager()
        result=[dict(manager.load(t),id=t) for t in manager.list_templates()]
        return result+[dict(json.loads(r['config']),id=r['id']) for r in self.rows('SELECT * FROM user_templates')]

    def save_template(self, payload):
        config=TemplateManager.validate(payload)
        tid=payload.get('id') or 'user_'+uid()
        if not tid.startswith('user_') or len(tid)!=37 or any(c not in '0123456789abcdef' for c in tid[5:]):raise ValueError('内置模板请另存为自己的模板')
        with self.connection() as db:
            old=db.execute('SELECT config FROM user_templates WHERE id=?',(tid,)).fetchone()
            config['version']=json.loads(old['config']).get('version',1)+1 if old else 1
            db.execute('INSERT OR REPLACE INTO user_templates VALUES(?,?)',(tid,json.dumps(config)))
        return tid

    def create_batch(self,payload,preview=False):
        if self.stopping.is_set():raise ValueError('软件正在关闭，不能创建任务')
        objects=payload.get('objects',[])
        template_ids=list(dict.fromkeys(payload.get('templates',[])))
        if not objects or not template_ids:raise ValueError('请选择生产对象和模板')
        copies=payload.get('copies',1)
        if type(copies) is not int or not 1<=copies<=1000:raise ValueError('生成数量必须为 1 到 1000 的整数')
        combination=payload.get('combination','all')
        order=payload.get('order','selected')
        if combination not in {'all','cycle','random'} or order not in {'selected','reverse','random'}:raise ValueError('组合或顺序模式无效')
        if len(objects)*len(template_ids)*copies>10000:raise ValueError('每批最多 10000 条任务')
        available={t['id']:t for t in self.templates()}
        if any(t not in available for t in template_ids):raise ValueError('模板不存在')
        mode=payload.get('music','none')
        if mode in {'ai','auto'}:raise ValueError('AI 自动选曲和音乐源等待接入，未执行选曲')
        if mode=='selected':mode='single'  # Preserve existing single-song callers; never emulate AI.
        if mode not in {'none','local','single','multi'}:raise ValueError('音乐模式无效')
        pool=list(dict.fromkeys(payload.get('music_ids') or ([payload['music_id']] if payload.get('music_id') else [])))
        if mode=='none':pool=[]
        if mode=='single' and len(pool)!=1:raise ValueError('指定单曲需要选择一首音乐')
        if mode=='multi' and not pool:raise ValueError('请选择本次音乐池')
        music_order=payload.get('music_order','cycle')
        if music_order not in {'cycle','random'}:raise ValueError('音乐分配方式无效')
        rng=random.Random(payload.get('seed'))
        defaults=self.snapshot()['settings']
        output_dir=self.check_output_dir(payload.get('output_dir') or defaults.get('output_dir'))
        batch=uid();jobs=[];previews=[];rotation={}
        records=self.local_records()
        with self.connection() as db:
            music_rows={}
            if mode=='local':pool=[r['id'] for r in db.execute("SELECT id FROM assets WHERE kind='music' AND deleted=0") if self.asset_path(r['id']).is_file()]
            for mid in pool:
                r=db.execute("SELECT * FROM assets WHERE id=? AND kind='music'",(mid,)).fetchone()
                if not r or r['deleted'] or not self.asset_path(mid).is_file():raise ValueError('音乐素材缺失')
                music_rows[mid]=dict(r)
            for obj in objects:
                pid=obj.get('product_id') or None;sid=obj.get('sku_id') or None
                origins=obj.get('product_ids') or ([pid] if pid else [])
                if len(origins)>1 and pid:raise ValueError('合集不能冒充单商品任务')
                if sid and (not pid or not db.execute('SELECT 1 FROM skus WHERE id=? AND product_id=?',(sid,pid)).fetchone()):raise ValueError('SKU 与商品不匹配')
                ids=[]
                for origin in origins:
                    if not db.execute('SELECT 1 FROM products WHERE id=?',(origin,)).fetchone():raise ValueError('商品不存在')
                    ids.extend(r[0] for r in db.execute("SELECT asset_id FROM product_assets WHERE product_id=? AND (? IS NULL OR sku_id IS NULL OR sku_id=?) ORDER BY CASE image_type WHEN 'main' THEN 0 WHEN 'detail' THEN 1 WHEN 'sku' THEN 2 ELSE 3 END,position,id",(origin,sid,sid)))
                if not origins:ids=obj.get('assets',[])
                ids=list(dict.fromkeys(ids))
                visual=[]
                for aid in ids:
                    row=db.execute('SELECT * FROM assets WHERE id=?',(aid,)).fetchone()
                    if not row:raise ValueError('素材不存在')
                    if row['kind'] in ('image','video'):
                        if row['deleted'] or not self.asset_path(aid).is_file():raise ValueError('素材文件缺失：'+row['name'])
                        visual.append(aid)
                if not visual:raise ValueError(f'{pid or "素材组"} 没有可用图片或视频')
                selected_order=payload.get('asset_order',[])
                if selected_order:visual=sorted(visual,key=lambda a:selected_order.index(a) if a in selected_order else len(selected_order)+visual.index(a))
                versions=[t for _ in range(copies) for t in template_ids] if combination=='all' else [template_ids[i%len(template_ids)] if combination=='cycle' else rng.choice(template_ids) for i in range(copies)]
                for version,tid in enumerate(versions,1):
                    ordered=list(visual)
                    if order=='reverse':ordered.reverse()
                    if order=='random':rng.shuffle(ordered)
                    overrides=payload.get('overrides',{})
                    if set(overrides)-{'transition','transition_duration','motion','music_volume'}:raise ValueError('模板覆盖字段无效')
                    cfg=dict(available[tid]);cfg.update(overrides)
                    cfg=TemplateManager.validate(cfg)
                    cfg['template_id']=tid;cfg['variant']=version
                    timing=payload.get('timing','total' if str(payload.get('duration','template'))!='template' else 'images')
                    if timing not in {'total','images'}:raise ValueError('时长模式无效')
                    per_asset=payload.get('asset_durations',{})
                    uniform=payload.get('image_duration')
                    transition=cfg['transition_duration']
                    if timing=='total':
                        if uniform is not None or per_asset:raise ValueError('总时长与逐图时间冲突，请选择一种时长模式')
                        total=seconds(payload.get('total_duration',payload.get('duration')), '总时长',36000)
                        frames=round((total+(len(ordered)-1)*transition)*30)
                        each,remainder=divmod(frames,len(ordered))
                        durations=[seconds((each+(i<remainder))/30) for i in range(len(ordered))]
                    else:durations=[seconds(per_asset.get(aid,uniform if uniform is not None else cfg['duration_per_image'])) for aid in ordered]
                    if any(d<=transition for d in durations):raise ValueError('转场必须短于每份素材展示时间')
                    cfg['durations']=durations;cfg['timing']=timing;cfg['expected_duration']=sum(durations)-(len(durations)-1)*transition
                    if timing=='total' and abs(cfg['expected_duration']-total)>.1:raise ValueError('素材数与30fps无法满足指定总时长，请改用逐图时长')
                    cfg['output_dir']=str(output_dir)
                    actual_products=[dict(db.execute('SELECT * FROM products WHERE id=?',(p,)).fetchone()) for p in origins]
                    for p in actual_products:p.update(json.loads(p['metadata']))
                    actual_skus=[dict(r) for p in origins for r in db.execute('SELECT * FROM skus WHERE product_id=? AND (? IS NULL OR id=?)',(p,sid,sid))]
                    asset_info=[dict(db.execute('SELECT * FROM assets WHERE id=?',(a,)).fetchone()) for a in ordered]
                    copy_options=payload.get('local_copy',{'mode':'local','language':'th','sources':[]})
                    if copy_options.get('mode','local')!='manual':
                        prior=[json.loads(v['content']).get('title','') for v in db.execute('SELECT content FROM videos')]
                        prior.extend(p.get('video_titles','') for p in actual_products)
                        generated,cfg['text_trace']=local_content.compose(records,actual_products,actual_skus,asset_info,copy_options,prior,len(jobs),rng,rotation)
                        cfg['local_copy']=copy_options
                    else:generated={}
                    cfg['music_mode']=mode;cfg['music_id']=None
                    choices=pool
                    if mode=='local':
                        choices,cfg['music_match']=local_content.match_music(records,[dict(r,metadata=json.loads(r['metadata'])) for r in music_rows.values()],cfg.get('text_trace',{}).get('tags',[]),cfg.get('tags',[]))
                        if not choices and not preview:raise ValueError('本地自动匹配未找到音乐；请补充标签，或选择单曲、多曲、无音乐。未联网或调用AI')
                    if choices:
                        chosen=local_content.allocate(choices,music_order,len(jobs),rng,rotation,'music')
                        cfg['music_id']=chosen
                        cfg['music_snapshot']=dict(json.loads(music_rows[chosen]['metadata']),Music_ID=chosen,filename=music_rows[chosen]['name'],source=music_rows[chosen]['source'])
                    cfg['music_playback']=payload.get('music_playback','loop')
                    if cfg['music_playback'] not in {'loop','trim'}:raise ValueError('音乐播放方式无效')
                    cfg['music_fade']=float(payload.get('music_fade',0))
                    if not math.isfinite(cfg['music_fade']) or not 0<=cfg['music_fade']<=cfg['expected_duration']/2:raise ValueError('音乐淡入淡出时间无效')
                    cfg['source_relations']=[dict(r) for aid in ordered for r in db.execute('SELECT pa.*,p.title,p.store,p.url FROM product_assets pa JOIN products p ON pa.product_id=p.id WHERE asset_id=?',(aid,)) if not origins or r['product_id'] in origins]
                    cfg['asset_snapshot']=[dict(db.execute('SELECT id,name,hash,path,source,metadata FROM assets WHERE id=?',(aid,)).fetchone()) for aid in ordered]
                    if cfg['music_id']:cfg['music_hash']=music_rows[cfg['music_id']]['hash']
                    explicit={k:v for k,v in payload.get('content',{}).items() if v or k in ('association','product_ids')}
                    cfg['publication_content']=self.validate_content(dict(generated,**explicit),cfg['source_relations'])
                    task_id=uid()
                    jobs.append((task_id,batch,pid,sid,json.dumps(ordered),tid,json.dumps(cfg), 'waiting',0,None,'',now()))
                    previews.append({'id':task_id,'product_id':pid,'assets':ordered,'config':cfg})
            if not preview:
                db.execute('INSERT INTO batches VALUES(?,?,?)',(batch,str(payload.get('name','')).strip() or '视频生产批次',now()))
                db.executemany('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',jobs)
        if not preview:self.start_worker()
        return {'batch':batch,'tasks':len(jobs),'preview':previews}

    def start_worker(self):
        with self.lock:
            if self.worker and self.worker.is_alive():return
            if self.stopping.is_set():return
            self.worker=threading.Thread(target=self._work,daemon=True);self.worker.start()

    def _work(self):
        from renderer.video_engine import VideoEngine,validate_output
        while not self.stopping.is_set():
            with self.connection() as db:
                t=db.execute("SELECT * FROM tasks WHERE status='waiting' ORDER BY created,id LIMIT 1").fetchone()
                if not t:
                    self.worker=None
                    self.apply_retention()
                    return
                t=dict(t);db.execute("UPDATE tasks SET status='running',progress=1 WHERE id=?",(t['id'],))
            generated=False
            try:
                cfg=json.loads(t['config']);assets=json.loads(t['assets'])
                files=[self.asset_path(a) for a in assets]
                music=self.asset_path(cfg['music_id']) if cfg.get('music_id') else None
                output=Path(cfg.get('output_dir',self.root/'output'))/f"{t['id']}.mp4"
                self.check_output_dir(output.parent)
                for item in cfg.get('asset_snapshot',[]):
                    if file_hash(self.asset_path(item['id']))!=item['hash']:raise ValueError('素材内容已变化：'+item['name'])
                if music and cfg.get('music_hash') and file_hash(music)!=cfg['music_hash']:raise ValueError('音乐文件内容已变化')
                cfg.setdefault('expected_duration',len(files)*cfg['duration_per_image']-(len(files)-1)*(cfg['transition_duration'] if cfg['transition']!='none' else 0))
                n=0
                def progress(message):
                    nonlocal n
                    n=min(95,n+max(1,90//(len(files)+2)))
                    with self.connection() as db:db.execute('UPDATE tasks SET progress=? WHERE id=?',(n,t['id']))
                VideoEngine(cache_root=self.root/'cache').generate(files,cfg,output,music,progress)
                generated=True
                validation=validate_output(output,cfg);duration=validation['duration']
                with self.connection() as db:
                    relative=str(output.resolve())
                    db.execute('INSERT INTO videos(id,task_id,path,duration,created,validation,content) VALUES(?,?,?,?,?,?,?)',(uid(),t['id'],relative,duration,now(),json.dumps(validation),json.dumps(cfg.get('publication_content',{}))))
                    db.execute("UPDATE tasks SET status='done',progress=100,output=?,error='' WHERE id=?",(relative,t['id']))
            except Exception as e:
                # A failed result is never retained as an output eligible for retry.
                # Rendered output belongs to this task; never remove another path.
                failed=Path(json.loads(t['config']).get('output_dir',self.root/'output'))/f"{t['id']}.mp4"
                error=str(e)
                if generated:
                    try:failed.unlink(missing_ok=True)
                    except OSError as cleanup_error:error+='；失败文件清理失败：'+str(cleanup_error)
                with self.connection() as db:db.execute("UPDATE tasks SET status='failed',error=? WHERE id=?",(error,t['id']))

    def retry(self,tid):
        self.check_task_inputs(tid)
        with self.connection() as db:
            result=db.execute("UPDATE tasks SET status='waiting',progress=0,error='' WHERE id=? AND status='failed'",(tid,))
            if result.rowcount!=1:raise ValueError('仅失败任务可以重试')
        self.start_worker()

    def regenerate(self,vid):
        with self.connection() as db:
            row=db.execute('SELECT t.* FROM videos v JOIN tasks t ON v.task_id=t.id WHERE v.id=?',(vid,)).fetchone()
            if not row:raise ValueError('视频不存在')
            self.check_task_inputs(row['id'])
            t=dict(row);new=uid()
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(new,t['batch_id'],t['product_id'],t['sku_id'],t['assets'],t['template'],t['config'],'waiting',0,None,'',now()))
        self.start_worker();return new

    def asset_path(self,aid):
        found=self.rows('SELECT path FROM assets WHERE id=?',(aid,))
        if not found:raise ValueError('素材不存在')
        p=(self.root/found[0]['path']).resolve()
        if not p.is_relative_to(self.root):raise ValueError('素材路径无效')
        if not p.is_file():raise ValueError('素材文件已清理或缺失')
        return p

    def video_path(self,vid):
        found=self.rows('SELECT path FROM videos WHERE id=?',(vid,))
        if not found:raise ValueError('视频不存在')
        p=(self.root/found[0]['path']).resolve()
        # Path comes only from a persisted task, never from an HTTP path parameter.
        if not p.is_file():raise ValueError('视频文件已清理或缺失')
        return p

    def trash(self,vid,restore=False):
        if restore:self.video_path(vid)
        with self.connection() as db:
            changed=db.execute('UPDATE videos SET trashed=? WHERE id=?',(0 if restore else 1,vid))
            if not changed.rowcount:raise ValueError('视频不存在')

    def queue_publication(self,vid,target=''):
        if not self.video_path(vid).is_file():raise ValueError('输出视频文件缺失，不能加入发布队列')
        with self.connection() as db:
            if not db.execute('SELECT id FROM videos WHERE id=? AND trashed=0',(vid,)).fetchone():raise ValueError('视频不存在或已在回收站')
            if db.execute("SELECT 1 FROM publications WHERE video_id=? AND target=? AND status='pending'",(vid,target)).fetchone():return
            db.execute('INSERT INTO publications(id,video_id,target,status,error,created,content) VALUES(?,?,?,?,?,?,?)',(uid(),vid,target,'pending','等待连接 TikTok 店铺环境',now(),db.execute('SELECT content FROM videos WHERE id=?',(vid,)).fetchone()[0]))

    def publish(self,pid):
        raise RuntimeError('等待连接 TikTok 店铺环境；尚未执行上传或发布')

    def snapshot(self):
        with self.connection() as db:
            result={name:[dict(r) for r in db.execute('SELECT * FROM '+table)] for name,table in [('products','products'),('assets','assets'),('skus','skus'),('links','product_assets'),('tasks','tasks'),('batches','batches'),('videos','videos'),('publications','publications'),('sync_tasks','sync_tasks'),('download_rules','download_rules')]}
            result['settings']={r['key']:json.loads(r['value']) for r in db.execute('SELECT * FROM settings')}
        result['stores']=list(dict.fromkeys(p['store'] for p in result['products'] if p['store']))
        result['storage']={'total':shutil.disk_usage(self.root).total,'free':shutil.disk_usage(self.root).free,'root':str(self.root),'output':str(self.root/'output'),'cache':sum(safe_size(p) for p in (self.root/'cache').rglob('*'))}
        result['videos'].sort(key=lambda v:(v['created'],v['id']),reverse=True)
        for a in result['assets']:
            a['available']=not a['deleted'] and (self.root/a['path']).is_file()
            a['metadata']=json.loads(a['metadata'])
        for v in result['videos']:
            v['resolved_path']=str((self.root/v['path']).resolve())
            v['available']=not v['deleted'] and Path(v['resolved_path']).is_file()
        result['templates']=self.templates()
        result['local_records']=self.local_records()
        for p in result['products']:p.update(json.loads(p['metadata']))
        result['settings']['music']={'auto':'ai','selected':'single'}.get(result['settings'].get('music'),result['settings'].get('music','local'))
        result['storage']['output']=result['settings'].get('output_dir') or str(self.root/'output')
        result['connection']='waiting'
        result['external']={'tiktok':'waiting','ai_copy':'waiting','ai_music':'waiting','pixabay':'waiting'}
        return result

    def cleanup(self,days=0):
        days=int(days)
        if days<0:raise ValueError('保留时间无效')
        with self.lock:
            if self.worker and self.worker.is_alive():raise ValueError('生产进行中，暂不清理缓存')
            cutoff=time.time()-days*86400;removed=0
            for directory in (self.root/'cache').iterdir():
                if directory.is_dir() and not directory.is_symlink() and directory.stat().st_mtime<cutoff:
                    removed+=sum(p.stat().st_size for p in directory.rglob('*') if p.is_file())
                    shutil.rmtree(directory)
            return removed


    def check_task_inputs(self, tid):
        rows=self.rows('SELECT assets,config FROM tasks WHERE id=?',(tid,))
        if not rows:raise ValueError('任务不存在')
        cfg=json.loads(rows[0]['config'])
        for aid in json.loads(rows[0]['assets'])+([cfg['music_id']] if cfg.get('music_id') else []):self.asset_path(aid)

    def local_records(self):
        return [dict(json.loads(r['config']),id=r['id'],version=r['version']) for r in self.rows('SELECT * FROM local_records WHERE deleted=0 ORDER BY rowid')]

    def save_local_record(self, payload):
        r=local_content.validate(payload)
        rid=r.get('id') or uid();identity=(r['kind'],local_content.norm(r['key']),r['language'])
        with self.connection() as db:
            old=db.execute('SELECT * FROM local_records WHERE id=?',(rid,)).fetchone()
            if r.get('id') and not old:raise ValueError('更新对象不存在；新增记录请留空ID')
            if old and (old['version']!=r.get('version') or tuple(old[k] for k in ('kind','key','language'))!=identity):raise ValueError('资料已更新或匹配身份不同，请重新载入；不覆盖其他对象')
            aliases={local_content.norm(r['key']),*r.get('aliases',[])}
            for other in self.local_records():
                if other['id']!=rid and other['kind']==r['kind'] and other['language']==r['language'] and aliases.intersection({local_content.norm(other['key']),*other.get('aliases',[])}):raise ValueError('匹配键或别名与已有资料冲突：'+other['key'])
            version=old['version']+1 if old else 1
            db.execute('INSERT INTO local_records(id,kind,key,language,config,version) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET config=excluded.config,version=excluded.version,deleted=0',(rid,*identity,json.dumps(r),version))
        return {'id':rid,'version':version}

    def delete_local_record(self, rid, version):
        with self.connection() as db:
            r=db.execute('UPDATE local_records SET deleted=1,version=version+1 WHERE id=? AND version=? AND deleted=0',(rid,version))
            if not r.rowcount:raise ValueError('资料已更新或删除，请重新载入')
        return {'deleted':rid}

    def import_local_records(self, rows):
        if not isinstance(rows,list) or not 1<=len(rows)<=10000:raise ValueError('导入需要1到10000条记录')
        result={'imported':[],'errors':[]}
        for index,row in enumerate(rows,1):
            try:result['imported'].append(dict(self.save_local_record(row),row=index))
            except (ValueError,TypeError,sqlite3.IntegrityError) as e:result['errors'].append({'row':index,'error':str(e)})
        return result

    def validate_content(self, content, sources=None):
        allowed={'title','caption','tags','opening','cta','cover','association','product_ids'}
        if set(content)-allowed:raise ValueError('发布文字字段无效')
        result={k:str(content.get(k,'')) for k in ('title','caption','tags','opening','cta','cover')}
        if any(len(v)>10000 for v in result.values()):raise ValueError('发布文字过长')
        mode=content.get('association','none')
        if mode not in {'none','source','selected'}:raise ValueError('商品关联模式无效')
        ids=content.get('product_ids',[]) if mode=='selected' else list(dict.fromkeys(r['product_id'] for r in (sources or []))) if mode=='source' else []
        if mode=='selected' and not ids:raise ValueError('请选择发布关联商品')
        for pid in ids:
            if not self.rows('SELECT id FROM products WHERE id=?',(pid,)):raise ValueError('关联商品不存在')
        result.update(association=mode,product_ids=ids)
        return result

    def save_content(self, vid, content):
        with self.connection() as db:
            row=db.execute('SELECT t.config FROM videos v JOIN tasks t ON v.task_id=t.id WHERE v.id=?',(vid,)).fetchone()
            if not row:raise ValueError('视频不存在')
            result=self.validate_content(content,json.loads(row['config']).get('source_relations',[]))
            db.execute('UPDATE videos SET content=? WHERE id=?',(json.dumps(result),vid))
        return result

    def update_music(self, aid, values):
        allowed={'title','author','source_url','license','tags'}
        if set(values)-allowed or any(not isinstance(v,str) or len(v)>2000 for v in values.values()):raise ValueError('音乐元数据无效')
        with self.connection() as db:
            row=db.execute("SELECT metadata FROM assets WHERE id=? AND kind='music'",(aid,)).fetchone()
            if not row:raise ValueError('音乐不存在')
            metadata=json.loads(row['metadata']);metadata.update(values)
            db.execute('UPDATE assets SET metadata=? WHERE id=?',(json.dumps(metadata),aid))
        return metadata

    def delete_asset(self, aid):
        with self.connection() as db:
            row=db.execute('SELECT * FROM assets WHERE id=?',(aid,)).fetchone()
            if not row:raise ValueError('素材不存在')
            for t in db.execute("SELECT assets,config FROM tasks WHERE status IN ('waiting','running','failed')"):
                if aid in json.loads(t['assets']) or json.loads(t['config']).get('music_id')==aid:raise ValueError('素材被待执行、运行或待重试任务使用，不能删除')
            path=(self.root/row['path']).resolve()
            owned=any(path.is_relative_to(self.root/n) for n in ('original_imports','downloads'))
            if not owned or path==Path(row['original_path']).resolve():raise ValueError('禁止删除用户原始文件')
            path.unlink(missing_ok=True)
            db.execute('UPDATE assets SET deleted=1 WHERE id=?',(aid,))
        return {'deleted':aid}

    def apply_retention(self):
        settings=self.snapshot()['settings'];errors=[];removed=0
        with self.lock:
            if self.worker and self.worker.is_alive():return {'bytes':0,'errors':[]}
            days=int(settings.get('cacheDays','7'))
            if days:removed+=self.cleanup(days)
            if settings.get('download_retention')=='after_tasks':
                for a in self.rows("SELECT id FROM assets WHERE source='tiktok' AND deleted=0"):
                    used=[t for t in self.rows('SELECT assets,status FROM tasks') if a['id'] in json.loads(t['assets'])]
                    if used and all(t['status']=='done' for t in used):
                        try:self.delete_asset(a['id'])
                        except (ValueError,OSError) as e:errors.append(str(e))
            policy=settings.get('output_retention','keep')
            if policy!='keep':
                accepted={'published'} if policy=='after_published' else {'uploaded','published'}
                for v in self.rows('SELECT * FROM videos WHERE deleted=0'):
                    pubs=self.rows('SELECT status FROM publications WHERE video_id=?',(v['id'],))
                    if pubs and all(p['status'] in accepted for p in pubs):
                        path=(self.root/v['path']).resolve()
                        # Only a file generated under the saved task identity may be removed.
                        if path.name!=v['task_id']+'.mp4':errors.append('输出归属不符，未清理');continue
                        try:
                            path.unlink(missing_ok=True)
                            with self.connection() as db:db.execute('UPDATE videos SET deleted=1 WHERE id=?',(v['id'],))
                        except OSError as e:errors.append(str(e))
        return {'bytes':removed,'errors':errors}


def safe_size(path):
    try:return path.stat().st_size if path.is_file() else 0
    except FileNotFoundError:return 0


def file_hash(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()
