"""Acceptance against the extracted original EXE, using WebView2 DOM + owned native controls."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
EVIDENCE=ROOT/'build'/'evidence';EVIDENCE.mkdir(parents=True,exist_ok=True)

def main():
    if sys.platform!='win32':raise SystemExit('Windows acceptance requires Windows')
    with tempfile.TemporaryDirectory(prefix="TVM 中文 O'Brien ",delete=False) as temporary:
        base=Path(temporary)
        with zipfile.ZipFile(ROOT/'dist'/'TikTokVideoMaker_Portable.zip') as z:z.extractall(base)
        app=base/'TikTokVideoMaker'
        sources=base/'素材';sources.mkdir()
        for p in (ROOT/'examples/input').iterdir():shutil.copy2(p,sources/p.name)
        output=base/'自选 输出';output.mkdir()
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        environment=os.environ.copy()
        # Debug transport is only enabled in this CI process; never written into the shipped app.
        environment['TVM_CDP_PORT']=str(port)
        process=subprocess.Popen([str(app/'TikTokVideoMaker.exe')],cwd=base,env=environment)
        exited_normally=False
        try:
            deadline=time.monotonic()+45
            while True:
                if process.poll() is not None:raise RuntimeError('Original EXE exited before WebView initialized')
                startup_log=app/'data'/'application.log'
                if startup_log.is_file() and 'Desktop startup failed' in startup_log.read_text(encoding='utf-8'):
                    raise RuntimeError('Original EXE reported a startup error; see windows-application.log')
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version',timeout=1) as response:
                        if response.status==200:
                            (EVIDENCE/'windows-cdp.json').write_text(json.dumps(json.load(response),indent=2),encoding='utf-8')
                            break
                except (OSError,urllib.error.URLError):
                    if time.monotonic()>deadline:raise RuntimeError('Acceptance CDP endpoint unavailable; this does not establish that the EXE or page failed to start')
                    time.sleep(.2)
            local_file=base/'本地资料.json';shutil.copy2(ROOT/'tests/local_fixture.json',local_file)
            fixture={'local_file':str(local_file),'local_records':json.loads(local_file.read_text(encoding='utf-8')),'pid':process.pid,'source':str(sources),'files':[str(p) for p in sources.iterdir()],'output':str(output)}
            fixture_path=base/'fixture.json';fixture_path.write_text(json.dumps(fixture),encoding='utf-8')
            environment.update(TVM_CDP=f'http://127.0.0.1:{port}',TVM_FIXTURE=str(fixture_path),PYTHON=sys.executable)
            subprocess.run(['node','tests/ui_business.cjs'],cwd=ROOT,env=environment,check=True,timeout=480)
            subprocess.run([sys.executable,'tests/windows_dialog.py',str(process.pid),'close'],cwd=ROOT,check=True,timeout=30)
            code=process.wait(timeout=60)
            if code!=0:raise RuntimeError(f'Normal EXE close returned {code}')
            exited_normally=True
            import sqlite3
            with sqlite3.connect(app/'data'/'workspace.sqlite3') as db:
                count=db.execute('SELECT COUNT(*) FROM videos').fetchone()[0]
                if count!=4:raise RuntimeError('Expected four actual rendered videos')
            # Restart the same EXE and verify persisted records through its original page.
            process=subprocess.Popen([str(app/'TikTokVideoMaker.exe')],cwd=base,env=environment)
            exited_normally=False
            time.sleep(2)
            subprocess.run(['node','tests/windows_restart.cjs',f'http://127.0.0.1:{port}'],cwd=ROOT,check=True,timeout=60)
            subprocess.run([sys.executable,'tests/windows_dialog.py',str(process.pid),'close'],cwd=ROOT,check=True,timeout=30)
            if process.wait(timeout=60)!=0:raise RuntimeError('Restarted EXE did not exit normally')
            exited_normally=True
            (EVIDENCE/'windows-acceptance.json').write_text(json.dumps({'passed':True,'platform':sys.getwindowsversion().build,'normal_exit':True,'restart':True,'real_videos':count,'source_commit':os.environ.get('GITHUB_SHA'),'external_platforms':'not_connected'},indent=2),encoding='utf-8')
        except Exception:
            # Diagnose only this test's EXE and descendants. A diagnostic failure never passes acceptance.
            with (EVIDENCE/'native-windows.txt').open('w',encoding='utf-8') as log:
                subprocess.run([sys.executable,'tests/windows_dialog.py',str(process.pid),'diagnose',str(EVIDENCE)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=30)
            script=f'''$all = Get-CimInstance Win32_Process
$owned = @({process.pid})
do {{ $more = @($all | Where-Object {{ $_.ParentProcessId -in $owned -and $_.ProcessId -notin $owned }} | Select-Object -ExpandProperty ProcessId); $owned += $more }} while ($more.Count)
$all | Where-Object {{ $_.ProcessId -in $owned }} | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Depth 3
Get-NetTCPConnection -State Listen | Where-Object {{ $_.OwningProcess -in $owned }} | Select-Object LocalAddress,LocalPort,OwningProcess | ConvertTo-Json
'''
            with (EVIDENCE/'owned-processes.txt').open('w',encoding='utf-8') as log:
                subprocess.run(['pwsh','-NoProfile','-Command',script],stdout=log,stderr=subprocess.STDOUT,timeout=30)
            raise
        finally:
            log=app/'data'/'application.log'
            if log.is_file():shutil.copy2(log,EVIDENCE/'windows-application.log')
            if not exited_normally and process.poll() is None:
                # Failure cleanup is explicitly NOT a passed exit test, and only this EXE is affected.
                (EVIDENCE/'forced-cleanup.txt').write_text('Acceptance failed; stopped only the owned test EXE.',encoding='utf-8')
                process.terminate();process.wait(timeout=20)
        # Failed runs retain their isolated directory for runner teardown and preserve the original error.
        # Normal WebView children can release profile files briefly after the host's normal exit.
        for attempt in range(20):
            try:shutil.rmtree(base);break
            except PermissionError:
                if attempt==19:raise
                time.sleep(.2)

if __name__=='__main__':main()
