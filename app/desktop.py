"""Desktop entry: one local window, one workspace, no cloud or account service."""
from __future__ import annotations
import json
import mimetypes
import os
from pathlib import Path
import secrets
import subprocess
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse,parse_qs,unquote
from core.paths import app_root
from core.workspace import Workspace
from core.workspace import now

class DesktopService:
    def __init__(self,workspace):
        self.workspace=workspace;self.window=None;self.token=secrets.token_urlsafe(32)
        service=self
        class Handler(BaseHTTPRequestHandler):
            def authorized(self):
                cookie=self.headers.get('Cookie','')
                host=self.headers.get('Host','')
                return ('tvm_session='+service.token) in cookie.split('; ') and host==f'127.0.0.1:{self.server.server_port}'
            def do_GET(self):
                parsed=urlparse(self.path)
                if parsed.path=='/start' and secrets.compare_digest(parse_qs(parsed.query).get('token',[''])[0],service.token):
                    self.send_response(303);self.send_header('Set-Cookie',f'tvm_session={service.token}; HttpOnly; SameSite=Strict; Path=/');self.send_header('Location','/');self.end_headers();return
                if not self.authorized():self.send_error(403);return
                try:
                    if parsed.path=='/api/state':self.json_response(service.workspace.snapshot());return
                    if parsed.path.startswith('/media/'):
                        parts=parsed.path.split('/')
                        path=service.workspace.video_path(parts[-1]) if parts[2]=='video' else service.workspace.asset_path(parts[-1])
                    else:
                        name=unquote(parsed.path).lstrip('/') or 'index.html'
                        if name not in {'index.html','app.js','styles.css','connected.js'}:self.send_error(404);return
                        path=app_root()/'ui'/name
                    if not path.is_file():self.send_error(404);return
                    size=path.stat().st_size;start=0;end=size-1;partial=False
                    raw=self.headers.get('Range')
                    if raw:
                        import re
                        match=re.fullmatch(r'bytes=(\d*)-(\d*)',raw)
                        if not match: self.send_error(416);return
                        a,b=match.groups()
                        if not a:
                            start=max(0,size-int(b))
                        else:
                            start=int(a);end=min(int(b),end) if b else end
                        if start>end or start>=size:self.send_error(416);return
                        partial=True
                    self.send_response(206 if partial else 200)
                    self.send_header('Content-Type',mimetypes.guess_type(str(path))[0] or 'application/octet-stream')
                    self.send_header('Content-Length',str(end-start+1));self.send_header('Accept-Ranges','bytes')
                    self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store')
                    if partial:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                    self.end_headers()
                    with path.open('rb') as f:
                        f.seek(start);remaining=end-start+1
                        while remaining>0:
                            chunk=f.read(min(1024*1024,remaining))
                            if not chunk:break
                            self.wfile.write(chunk);remaining-=len(chunk)
                except (BrokenPipeError,ConnectionResetError):pass
                except Exception as e:self.json_response({'error':str(e)},400)
            def do_POST(self):
                if not self.authorized():self.send_error(403);return
                origin=self.headers.get('Origin')
                if origin and origin!=f'http://127.0.0.1:{self.server.server_port}':self.send_error(403);return
                try:
                    length=int(self.headers.get('Content-Length',0))
                    if not 0<length<2_000_000:raise ValueError('请求大小无效')
                    payload=json.loads(self.rfile.read(length))
                    self.json_response(service.command(urlparse(self.path).path.removeprefix('/api/'),payload))
                except Exception as e:self.json_response({'error':str(e)},400)
            def json_response(self,value,status=200):
                body=json.dumps(value,ensure_ascii=False).encode()
                self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
    @property
    def url(self):return f'http://127.0.0.1:{self.server.server_port}/start?token={self.token}'
    def start(self):self.thread.start()
    def command(self,name,p):
        w=self.workspace
        if name=='product':return {'id':w.product(p)}
        if name=='link':w.link_assets(p['product_id'],p['ids'],p.get('metadata'));return {'ok':True}
        if name=='batch':return w.create_batch(p)
        if name=='retry':w.retry(p['id']);return {'ok':True}
        if name=='regenerate':return {'id':w.regenerate(p['id'])}
        if name=='trash':w.trash(p['id'],bool(p.get('restore')));return {'ok':True}
        if name=='queue':w.queue_publication(p['id'],str(p.get('target','')));return {'ok':True}
        if name=='publish':return w.publish(p['id'])
        if name=='settings':return w.save_settings(p)
        if name=='download-rule':return {'id':w.save_download_rule(p)}
        if name=='sync-request':return {'id':w.save_sync_request(p)}
        if name=='cleanup':return {'bytes':w.cleanup(int(p.get('days',0)))}
        if name=='open':
            path=w.video_path(p['id']) if p.get('id') else w.root/'output'
            if p.get('folder') and path.is_file():path=path.parent
            if not path.exists():raise ValueError('文件不存在')
            if os.name=='nt':os.startfile(path)
            else:subprocess.Popen(['xdg-open',str(path)])
            return {'ok':True}
        if name in {'import','import-products'}:
            if self.window is None:raise RuntimeError('文件选择需要桌面窗口')
            import webview
            if name=='import':
                types=('素材 (*.png;*.jpg;*.jpeg;*.webp;*.mp4;*.mov;*.mkv;*.webm;*.mp3;*.wav;*.m4a;*.aac;*.flac)',)
                paths=self.window.create_file_dialog(webview.FileDialog.FOLDER if p.get('folder') else webview.FileDialog.OPEN,allow_multiple=not p.get('folder'),file_types=types)
                return w.import_assets(paths or [])
            paths=self.window.create_file_dialog(webview.FileDialog.OPEN,file_types=('CSV (*.csv)',))
            if not paths:return {'count':0}
            import csv
            with open(paths[0],encoding='utf-8-sig',newline='') as f:
                rows=list(csv.DictReader(f))
            if any(not r.get('id','').strip() or not r.get('title','').strip() for r in rows):raise ValueError('每行都需要 id 和 title 字段')
            for row in rows:row['skus']=row.get('skus','').split('|')
            # Validate the full file before beginning a single product import transaction.
            with w.connection() as db:
                for row in rows:
                    if len(row['id'])>150 or len(row['title'])>1000:raise ValueError('商品字段过长')
                for row in rows:
                    db.execute('INSERT INTO products VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,store=excluded.store,url=excluded.url',(row['id'].strip(),row['title'].strip(),row.get('store',''),row.get('url',''),now()))
                    for sku in dict.fromkeys(s.strip() for s in row['skus'] if s.strip()):
                        db.execute('INSERT OR IGNORE INTO skus(id,product_id,name) VALUES(?,?,?)',(secrets.token_hex(16),row['id'].strip(),sku))
            return {'count':len(rows)}
        raise ValueError('未知操作')
    def close(self):
        self.workspace.stopping.set()
        worker=self.workspace.worker
        if worker and worker.is_alive():worker.join()
        self.server.shutdown();self.server.server_close()

def main():
    import webview
    lock_file=None
    if os.name=='nt':
        import msvcrt
        data_dir=app_root()/'data';data_dir.mkdir(parents=True,exist_ok=True)
        lock_file=(data_dir/'instance.lock').open('a+b')
        lock_file.write(b'\0');lock_file.flush();lock_file.seek(0)
        try:msvcrt.locking(lock_file.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            lock_file.close()
            raise RuntimeError('TikTokVideoMaker 已在运行，请使用已打开的窗口')
    fixed_runtime=app_root()/'runtime'/'webview2'
    if os.name=='nt':
        if not (fixed_runtime/'msedgewebview2.exe').is_file():
            raise RuntimeError('便携版 WebView2 运行组件缺失')
        webview.settings['WEBVIEW2_RUNTIME_PATH']=str(fixed_runtime)
    webview.settings['ALLOW_DOWNLOADS']=True
    workspace=Workspace(app_root()/'data')
    service=DesktopService(workspace);service.start()
    service.window=webview.create_window('TikTokVideoMaker',service.url,width=1440,height=960,min_size=(1050,720),background_color='#f4f7fc')
    def closing():
        if workspace.worker and workspace.worker.is_alive():
            return service.window.create_confirmation_dialog('正在生成视频','关闭将等待当前视频写入完成，其余任务保留到下次启动。是否关闭？')
    service.window.events.closing+=closing
    try:
        retention=int(workspace.snapshot()['settings'].get('cacheDays','7'))
        if retention:workspace.cleanup(retention)
        workspace.start_worker()
        webview.start(private_mode=False,storage_path=str(workspace.root/'browser'),gui='edgechromium' if os.name=='nt' else None)
    finally:
        service.close()
        if lock_file:lock_file.close()
