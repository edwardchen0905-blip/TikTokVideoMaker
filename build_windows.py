"""Build the complete portable application on Windows. Never relabel scripts as EXEs."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import json
import hashlib
import importlib.metadata
import zipfile

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
    if 'WEBVIEW2_RUNTIME_PATH' not in webview.settings:
        raise SystemExit('Installed pywebview lacks fixed WebView2 support; install requirements.txt before building')
    # pywebview registers its own hook for Windows libraries and JavaScript resources.
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--onedir','--contents-directory','.',
        '--name','TikTokVideoMaker','--copy-metadata','pywebview','--copy-metadata','pythonnet','--add-data','ui;ui',
        'tiktok_video_maker.py'],cwd=root,check=True)
    executable=root/'dist'/'TikTokVideoMaker'/'TikTokVideoMaker.exe'
    if not executable.is_file():raise SystemExit('EXE was not produced')
    # Keep the complete browser/media runtime outside PyInstaller's dependency scan.
    # Scanning its independent executables copied browser DLLs into the app root.
    shutil.copytree(root/'runtime',executable.parent/'runtime')
    shutil.copy2(root/'docs'/'LOCAL_LIBRARY.md',executable.parent/'本地资料说明.md')
    print('Built executable:',executable)
    print('Windows interactive acceptance must pass before this is delivered.')
    manifest={'source_commit':os.environ.get('GITHUB_SHA'),'python':sys.version,'dependencies':{n:importlib.metadata.version(n) for n in ('pywebview','pyinstaller','pythonnet')},'runtime':json.loads((root/'runtime'/'build-inputs.json').read_text(encoding='utf-8'))}
    (executable.parent/'build-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    # A single package contains the app, interpreter, renderer and fixed browser runtime.
    archive=root/'dist'/'TikTokVideoMaker_Portable.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as bundle:
        for path in sorted(executable.parent.rglob('*')):
            if path.is_file():bundle.write(path,path.relative_to(root/'dist'))
    (root/'dist'/'TikTokVideoMaker_Portable.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest(),encoding='ascii')

if __name__=='__main__':main()
