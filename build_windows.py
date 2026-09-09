"""Build the complete portable application on Windows. Never relabel scripts as EXEs."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import json
import hashlib
import importlib.metadata

root=Path(__file__).resolve().parent

def main():
    if sys.platform!='win32':
        raise SystemExit('Windows build required. This environment cannot produce or verify the portable Windows executable.')
    os.environ['PATH']=str(root/'runtime')+os.pathsep+os.environ.get('PATH','')
    required=[root/'runtime'/'ffmpeg.exe',root/'runtime'/'ffprobe.exe',root/'runtime'/'webview2'/'msedgewebview2.exe']
    for binary in required:
        if not binary.is_file():
            raise SystemExit(f'Missing Windows runtime binary: {binary}')
        with binary.open('rb') as stream:
            if stream.read(2)!=b'MZ':raise SystemExit(f'Invalid Windows runtime binary: {binary}')
    # Builder dependencies belong to the build machine, not the user's machine.
    import PyInstaller
    import webview
    subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=root,check=True)
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--onedir','--contents-directory','.',
        '--name','TikTokVideoMaker','--collect-all','webview','--copy-metadata','pywebview','--copy-metadata','pythonnet','--add-data','ui;ui','--add-data','templates;templates',
        '--add-data','runtime;runtime','tiktok_video_maker.py'],cwd=root,check=True)
    executable=root/'dist'/'TikTokVideoMaker'/'TikTokVideoMaker.exe'
    if not executable.is_file():raise SystemExit('EXE was not produced')
    shutil.copy2(root/'docs'/'LOCAL_LIBRARY.md',executable.parent/'本地资料说明.md')
    print('Built executable:',executable)
    print('Windows interactive acceptance must pass before this is delivered.')
    manifest={'source_commit':os.environ.get('GITHUB_SHA'),'python':sys.version,'dependencies':{n:importlib.metadata.version(n) for n in ('pywebview','pyinstaller','pythonnet')},'runtime':json.loads((root/'runtime'/'build-inputs.json').read_text(encoding='utf-8'))}
    (executable.parent/'build-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    # A single package contains the app, interpreter, renderer and fixed browser runtime.
    archive=Path(shutil.make_archive(str(root/'dist'/'TikTokVideoMaker_Portable'),'zip',root/'dist','TikTokVideoMaker'))
    (root/'dist'/'TikTokVideoMaker_Portable.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest(),encoding='ascii')

if __name__=='__main__':main()
