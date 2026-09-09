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

KINDS = {'.png':'image','.jpg':'image','.jpeg':'image','.webp':'image','.mp4':'video','.mov':'video','.mkv':'video','.webm':'video','.mp3':'music','.wav':'music','.m4a':'music','.aac':'music','.flac':'music'}

def uid(): return uuid.uuid4().hex

def now(): return time.strftime('%Y-%m-%d %H:%M:%S')

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
        allowed={'source','template','duration','music','cacheDays','density'}
        if set(values)-allowed: raise ValueError('设置字段无效')
        if str(values.get('cacheDays','7')) not in {'0','1','7','30'}: raise ValueError('缓存保留时间无效')
        with self.connection() as db:
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
            else: db.execute('INSERT INTO products VALUES(?,?,?,?,?)',(pid,title,str(p.get('store','')),str(p.get('url','')),now()))
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
                    if old: imported.append(old['id']);continue
                    aid=uid();dest=self.root/'original_imports'/aid/p.name;dest.parent.mkdir()
                    try:
                        shutil.copy2(p,dest)
                        db.execute('INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?)',(aid,p.name,kind,'local',str(p),str(dest.relative_to(self.root)),fingerprint,p.stat().st_size,now()))
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

    def create_batch(self,payload):
        from core.template_manager import TemplateManager
        objects=payload.get('objects',[]);template_ids=list(dict.fromkeys(payload.get('templates',[])))
        if not objects or not template_ids: raise ValueError('请选择生产对象和模板')
        if len(objects)*len(template_ids)>10000: raise ValueError('每批最多 10000 条任务')
        configs={t:TemplateManager().load(t) for t in template_ids}
        batch=uid();jobs=[]
        with self.connection() as db:
            for obj in objects:
                pid=obj.get('product_id') or None;sid=obj.get('sku_id') or None
                if pid:
                    if not db.execute('SELECT 1 FROM products WHERE id=?',(pid,)).fetchone():raise ValueError('商品不存在')
                    ids=list(dict.fromkeys(r[0] for r in db.execute("SELECT asset_id FROM product_assets WHERE product_id=? AND (sku_id IS NULL OR sku_id=?) ORDER BY CASE image_type WHEN 'main' THEN 0 WHEN 'detail' THEN 1 WHEN 'sku' THEN 2 ELSE 3 END,position,id",(pid,sid))))
                else:ids=list(dict.fromkeys(obj.get('assets',[])))
                if sid and (not pid or not db.execute('SELECT 1 FROM skus WHERE id=? AND product_id=?',(sid,pid)).fetchone()): raise ValueError('SKU 与商品不匹配')
                visual=[]
                for aid in ids:
                    a=db.execute('SELECT * FROM assets WHERE id=?',(aid,)).fetchone()
                    if not a:raise ValueError('素材不存在')
                    if a['kind'] in ('image','video'):
                        if not (self.root/a['path']).is_file():raise ValueError('素材文件缺失')
                        visual.append(aid)
                if not visual:raise ValueError(f'{pid or "素材组"} 没有可生成视频的图片或视频素材')
                for tid in template_ids:
                    cfg=dict(configs[tid]);music_id=payload.get('music_id') or None
                    mode=payload.get('music','none')
                    if mode=='auto':
                        found=db.execute("SELECT id FROM assets WHERE kind='music' ORDER BY created,id LIMIT 1").fetchone()
                        if not found:raise ValueError('音乐库为空，无法自动匹配')
                        music_id=found[0]
                    if mode=='selected' and not music_id:raise ValueError('请选择音乐素材')
                    if music_id and not db.execute("SELECT 1 FROM assets WHERE id=? AND kind='music'",(music_id,)).fetchone():raise ValueError('音乐素材无效')
                    cfg['music_id']=music_id
                    target=str(payload.get('duration','template'))
                    if target!='template':
                        if target not in {'15','30'}:raise ValueError('不支持的视频时长')
                        cfg['duration_per_image']=(float(target)+(len(visual)-1)*cfg['transition_duration'])/len(visual)
                        if cfg['duration_per_image']<=cfg['transition_duration']:raise ValueError('素材过多，无法匹配选定时长')
                    jobs.append((uid(),batch,pid,sid,json.dumps(visual),tid,json.dumps(cfg), 'waiting',0,None,'',now()))
            db.execute('INSERT INTO batches VALUES(?,?,?)',(batch,str(payload.get('name','')).strip() or '视频生产批次',now()))
            db.executemany('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',jobs)
        self.start_worker()
        return {'batch':batch,'tasks':len(jobs)}

    def start_worker(self):
        with self.lock:
            if self.worker and self.worker.is_alive():return
            self.stopping.clear()
            self.worker=threading.Thread(target=self._work,daemon=True);self.worker.start()

    def _work(self):
        from renderer.video_engine import VideoEngine,probe_video
        while not self.stopping.is_set():
            with self.connection() as db:
                t=db.execute("SELECT * FROM tasks WHERE status='waiting' ORDER BY created,id LIMIT 1").fetchone()
                if not t:
                    self.worker=None
                    return
                t=dict(t);db.execute("UPDATE tasks SET status='running',progress=1 WHERE id=?",(t['id'],))
            try:
                cfg=json.loads(t['config']);assets=json.loads(t['assets'])
                files=[self.asset_path(a) for a in assets]
                music=self.asset_path(cfg['music_id']) if cfg.get('music_id') else None
                output=self.root/'output'/f"{t['id']}.mp4"
                n=0
                def progress(message):
                    nonlocal n
                    n=min(95,n+max(1,90//(len(files)+2)))
                    with self.connection() as db:db.execute('UPDATE tasks SET progress=? WHERE id=?',(n,t['id']))
                VideoEngine(cache_root=self.root/'cache').generate(files,cfg,output,music,progress)
                info=probe_video(output);duration=float(info['format']['duration'])
                if not math.isfinite(duration) or duration<=0:raise ValueError('生成的视频时长无效')
                with self.connection() as db:
                    relative=str(output.relative_to(self.root))
                    db.execute('INSERT INTO videos VALUES(?,?,?,?,?,0)',(uid(),t['id'],relative,duration,now()))
                    db.execute("UPDATE tasks SET status='done',progress=100,output=?,error='' WHERE id=?",(relative,t['id']))
            except Exception as e:
                # A failed result is never retained as an output eligible for retry.
                (self.root/'output'/f"{t['id']}.mp4").unlink(missing_ok=True)
                with self.connection() as db:db.execute("UPDATE tasks SET status='failed',error=? WHERE id=?",(str(e),t['id']))

    def retry(self,tid):
        with self.connection() as db:
            result=db.execute("UPDATE tasks SET status='waiting',progress=0,error='' WHERE id=? AND status='failed'",(tid,))
            if result.rowcount!=1:raise ValueError('仅失败任务可以重试')
        self.start_worker()

    def regenerate(self,vid):
        with self.connection() as db:
            row=db.execute('SELECT t.* FROM videos v JOIN tasks t ON v.task_id=t.id WHERE v.id=?',(vid,)).fetchone()
            if not row:raise ValueError('视频不存在')
            t=dict(row);new=uid()
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(new,t['batch_id'],t['product_id'],t['sku_id'],t['assets'],t['template'],t['config'],'waiting',0,None,'',now()))
        self.start_worker();return new

    def asset_path(self,aid):
        found=self.rows('SELECT path FROM assets WHERE id=?',(aid,))
        if not found:raise ValueError('素材不存在')
        p=(self.root/found[0]['path']).resolve()
        if not p.is_relative_to(self.root):raise ValueError('素材路径无效')
        return p

    def video_path(self,vid):
        found=self.rows('SELECT path FROM videos WHERE id=?',(vid,))
        if not found:raise ValueError('视频不存在')
        p=(self.root/found[0]['path']).resolve()
        if not p.is_relative_to(self.root/'output'):raise ValueError('输出路径无效')
        return p

    def trash(self,vid,restore=False):
        with self.connection() as db:
            changed=db.execute('UPDATE videos SET trashed=? WHERE id=?',(0 if restore else 1,vid))
            if not changed.rowcount:raise ValueError('视频不存在')

    def queue_publication(self,vid,target=''):
        if not self.video_path(vid).is_file():raise ValueError('输出视频文件缺失，不能加入发布队列')
        with self.connection() as db:
            if not db.execute('SELECT id FROM videos WHERE id=? AND trashed=0',(vid,)).fetchone():raise ValueError('视频不存在或已在回收站')
            if db.execute("SELECT 1 FROM publications WHERE video_id=? AND target=? AND status='pending'",(vid,target)).fetchone():return
            db.execute('INSERT INTO publications VALUES(?,?,?,?,?,?)',(uid(),vid,target,'pending','等待连接 TikTok 店铺环境',now()))

    def publish(self,pid):
        raise RuntimeError('等待连接 TikTok 店铺环境；尚未执行上传或发布')

    def snapshot(self):
        with self.connection() as db:
            result={name:[dict(r) for r in db.execute('SELECT * FROM '+table)] for name,table in [('products','products'),('assets','assets'),('skus','skus'),('links','product_assets'),('tasks','tasks'),('batches','batches'),('videos','videos'),('publications','publications'),('sync_tasks','sync_tasks'),('download_rules','download_rules')]}
            result['settings']={r['key']:json.loads(r['value']) for r in db.execute('SELECT * FROM settings')}
        result['stores']=list(dict.fromkeys(p['store'] for p in result['products'] if p['store']))
        result['storage']={'total':shutil.disk_usage(self.root).total,'free':shutil.disk_usage(self.root).free,'root':str(self.root),'output':str(self.root/'output'),'cache':sum(p.stat().st_size for p in (self.root/'cache').rglob('*') if p.is_file())}
        result['connection']='waiting'
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
