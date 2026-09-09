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
import hashlib
from contextlib import closing

ROOT=Path(__file__).resolve().parents[1]
EVIDENCE=ROOT/'build'/'evidence';EVIDENCE.mkdir(parents=True,exist_ok=True)

def record_stage(name,**details):
    with (EVIDENCE/'windows-stages.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps({'name':name,**details})+'\n')

def close_normally(process,app,port,label):
    subprocess.run([sys.executable,'tests/windows_dialog.py',str(process.pid),'close'],cwd=ROOT,check=True,timeout=30)
    code=process.wait(timeout=60)
    if code!=0:raise RuntimeError(f'{label}: normal EXE close returned {code}')
    log=(app/'data'/'application.log').read_text(encoding='utf-8')
    required=2 if label=='restart' else 1
    if log.count('Normal service shutdown completed')<required:raise RuntimeError(f'{label}: normal shutdown log missing')
    record_stage(label+'-exe-exited',pid=process.pid,exit_code=code,shutdown_log=True)
    record_stage(label+'-normal-exit',pid=process.pid,exit_code=code,shutdown_log=True)
    deadline=time.monotonic()+20
    attempt=0
    diagnostic={'port':port}
    try:
        while True:
            attempt+=1
            directory=EVIDENCE/f'{label}-exit-{attempt}';directory.mkdir()
            # Port cleanup is independent of the already verified EXE exit.
            state=capture_processes(process.pid,directory,port=port,require_running=False)
            listeners=[row for row in state['listeners'] if row['LocalPort']==port]
            if not listeners:
                diagnostic.update(status='closed',listeners=[]);break
            if time.monotonic()>=deadline:
                diagnostic.update(status='listening',listeners=listeners);break
            time.sleep(.2)
    except Exception as error:
        diagnostic.update(status='unknown',error=repr(error))
    record_stage(label+'-cdp-diagnostic',**diagnostic)
    return diagnostic

def capture_processes(pid,directory=EVIDENCE,*,port=None,require_running=True):
    port_filter=f' -or $_.LocalPort -eq {int(port)}' if port is not None else ''
    script=f'''$ErrorActionPreference = 'Stop'
    $all = Get-CimInstance Win32_Process
    $owned = @({pid})
    do {{ $more = @($all | Where-Object {{ $_.ParentProcessId -in $owned -and $_.ProcessId -notin $owned }} | Select-Object -ExpandProperty ProcessId); $owned += $more }} while ($more.Count)
    $listeners = @(Get-NetTCPConnection -State Listen | Where-Object {{ $_.OwningProcess -in $owned{port_filter} }} | Select-Object LocalAddress,LocalPort,OwningProcess)
    $processes = @($all | Where-Object {{ $_.ProcessId -in $owned -or $_.ProcessId -in $listeners.OwningProcess }} | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine)
    @{{processes=$processes;listeners=$listeners}} | ConvertTo-Json -Depth 4
    '''
    with (directory/'owned-processes.json').open('w',encoding='utf-8') as log:
        subprocess.run(['pwsh','-NoProfile','-Command',script],stdout=log,stderr=subprocess.STDOUT,timeout=30,check=True)
    result=json.loads((directory/'owned-processes.json').read_text(encoding='utf-8-sig'))
    if not isinstance(result['processes'],list) or not isinstance(result['listeners'],list):raise RuntimeError('Invalid Windows process/listener evidence')
    if require_running and not any(p['ProcessId']==pid for p in result['processes']):raise RuntimeError('Test EXE missing from process evidence')
    return result

def main():
    if sys.platform!='win32':raise SystemExit('Windows acceptance requires Windows')
    archive=ROOT/'dist'/'TikTokVideoMaker_Portable.zip'
    archive_sha=hashlib.sha256(archive.read_bytes()).hexdigest()
    expected_sha=(ROOT/'dist'/'TikTokVideoMaker_Portable.sha256').read_text().strip()
    if len(expected_sha)!=64 or any(c not in '0123456789abcdef' for c in expected_sha):raise RuntimeError('Invalid preserved candidate SHA-256')
    if archive_sha!=expected_sha:raise RuntimeError('Candidate archive SHA-256 mismatch')
    (EVIDENCE/'candidate-identity.json').write_text(json.dumps({'sha256':archive_sha,'test_commit':os.environ.get('GITHUB_SHA'),'candidate_run_id':os.environ.get('CANDIDATE_RUN_ID') or os.environ.get('GITHUB_RUN_ID')},indent=2),encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix="TVM 中文 O'Brien ",delete=False) as temporary:
        base=Path(temporary)
        with zipfile.ZipFile(ROOT/'dist'/'TikTokVideoMaker_Portable.zip') as z:z.extractall(base)
        app=base/'TikTokVideoMaker'
        shutil.copy2(app/'build-manifest.json',EVIDENCE/'candidate-build-manifest.json')
        sources=base/'素材';sources.mkdir()
        for p in (ROOT/'examples/input').iterdir():shutil.copy2(p,sources/p.name)
        from PIL import Image,ImageDraw
        for index,color in enumerate(('navy','maroon','darkgreen'),3):
            picture=Image.new('RGB',(360,360),'white');draw=ImageDraw.Draw(picture)
            draw.rectangle((0,0,359,359),outline=color,width=16);draw.text((170,170),str(index),fill=color)
            picture.save(sources/f'验收方图{index}.png')
        output=base/'自选 输出';output.mkdir()
        alternate_output=base/'更改后的默认输出';alternate_output.mkdir()
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        environment=os.environ.copy()
        # Debug transport is only enabled in this CI process; never written into the shipped app.
        environment['TVM_CDP_PORT']=str(port)
        process=subprocess.Popen([str(app/'TikTokVideoMaker.exe')],cwd=base,env=environment)
        record_stage('original-exe-started',pid=process.pid,sha256=archive_sha)
        exited_normally=False
        failed=False
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
            products_file=base/'商品资料.csv';products_file.write_text('id,title,skus\nCSV-P1,CSV导入商品,图名-材质-颜色\n',encoding='utf-8-sig')
            originals=[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in [*sources.iterdir(),local_file,products_file]]
            fixture={'local_file':str(local_file),'local_records':json.loads(local_file.read_text(encoding='utf-8')),'pid':process.pid,'source':str(sources),'files':[str(p) for p in sources.iterdir()],'output':str(output),'alternate_output':str(alternate_output),'products_file':str(products_file),'originals':originals}
            fixture_path=base/'fixture.json';fixture_path.write_text(json.dumps(fixture),encoding='utf-8')
            environment.update(TVM_CDP=f'http://127.0.0.1:{port}',TVM_FIXTURE=str(fixture_path),PYTHON=sys.executable)
            subprocess.run(['node','tests/ui_business.cjs'],cwd=ROOT,env=environment,check=True,timeout=480)
            if os.environ.get('TVM_CAPTURE_ONLY')=='1':
                if not (EVIDENCE/'windows-capture.json').is_file():raise RuntimeError('Window capture did not reach the required stage')
                capture_processes(process.pid)
                return
            diagnostics=[close_normally(process,app,port,'first')]
            exited_normally=True
            import sqlite3
            with closing(sqlite3.connect(app/'data'/'workspace.sqlite3')) as db:
                count=db.execute('SELECT COUNT(*) FROM videos').fetchone()[0]
                if count!=5:raise RuntimeError('Expected five actual rendered videos covering three resolutions')
            # Restart the same EXE and verify persisted records through its original page.
            # A leftover diagnostic endpoint must not become the restart test's target.
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            environment['TVM_CDP_PORT']=str(port)
            process=subprocess.Popen([str(app/'TikTokVideoMaker.exe')],cwd=base,env=environment)
            exited_normally=False
            record_stage('same-exe-restarted',pid=process.pid)
            subprocess.run(['node','tests/windows_restart.cjs',f'http://127.0.0.1:{port}'],cwd=ROOT,check=True,timeout=120)
            if process.poll() is not None:raise RuntimeError('Restarted EXE exited during recovery verification')
            diagnostics.append(close_normally(process,app,port,'restart'))
            exited_normally=True
            if hashlib.sha256(archive.read_bytes()).hexdigest()!=archive_sha:raise RuntimeError('Candidate archive changed during acceptance')
        except Exception as original_error:
            failed=True
            record_stage('failed',error=repr(original_error))
            # Diagnose only this test's EXE and descendants. A diagnostic failure never passes acceptance.
            try:
                if process.poll() is None:
                    with (EVIDENCE/'native-windows.txt').open('w',encoding='utf-8') as log:
                        subprocess.run([sys.executable,'tests/windows_dialog.py',str(process.pid),'diagnose',str(EVIDENCE)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=45,check=True)
                else:record_stage('native-diagnostic-not-applicable',pid=process.pid,exit_code=process.returncode)
            except Exception as diagnostic_error:record_stage('diagnostic-failed',error=repr(diagnostic_error))
            raise
        finally:
            log=app/'data'/'application.log'
            if log.is_file():shutil.copy2(log,EVIDENCE/'windows-application.log')
            if not exited_normally and process.poll() is None:
                # Failure cleanup is explicitly NOT a passed exit test, and only this EXE is affected.
                reason='Evidence-only collection ended; no normal-exit acceptance was performed.' if os.environ.get('TVM_CAPTURE_ONLY')=='1' else 'Acceptance failed; stopped only the owned test EXE.'
                (EVIDENCE/'forced-cleanup.txt').write_text(reason,encoding='utf-8')
                process.terminate();process.wait(timeout=20)
            if failed:
                # Snapshot only after the failed process stops writing SQLite/output files.
                # This evidence belongs to its own artifact, never to the candidate ZIP.
                try:
                    failure=EVIDENCE/'failure-workspace';failure.mkdir(exist_ok=True)
                    (failure/'location.json').write_text(json.dumps({'temporary_directory':str(base),'candidate_sha256':archive_sha},ensure_ascii=False,indent=2),encoding='utf-8')
                    for source in (app/'data',output,alternate_output,sources):
                        if source.is_dir():shutil.copytree(source,failure/source.name,dirs_exist_ok=True)
                except Exception as preservation_error:record_stage('failure-preservation-failed',error=repr(preservation_error))
        # Failed runs retain their isolated directory for runner teardown and preserve the original error.
        # Normal WebView children can release profile files briefly after the host's normal exit.
        for attempt in range(20):
            try:shutil.rmtree(base);break
            except PermissionError:
                if attempt==19:raise
                time.sleep(.2)
        (EVIDENCE/'windows-acceptance.json').write_text(json.dumps({'passed':True,'platform':sys.getwindowsversion().build,'normal_exit':True,'restart':True,'test_cleanup':True,'real_videos':count,'cdp_diagnostics':diagnostics,'test_commit':os.environ.get('GITHUB_SHA'),'candidate_sha256':archive_sha,'candidate_run_id':os.environ.get('CANDIDATE_RUN_ID') or os.environ.get('GITHUB_RUN_ID'),'external_platforms':'not_connected'},indent=2),encoding='utf-8')

if __name__=='__main__':main()
