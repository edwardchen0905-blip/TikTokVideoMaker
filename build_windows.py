"""Build the complete portable application on Windows. Never relabel scripts as EXEs."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

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
        '--name','TikTokVideoMaker','--collect-all','webview','--add-data','ui;ui','--add-data','templates;templates',
        '--add-data','runtime;runtime','tiktok_video_maker.py'],cwd=root,check=True)
    executable=root/'dist'/'TikTokVideoMaker'/'TikTokVideoMaker.exe'
    if not executable.is_file():raise SystemExit('EXE was not produced')
    print('Built executable:',executable)
    print('Windows interactive acceptance must pass before this is delivered.')
    # A single package contains the app, interpreter, renderer and fixed browser runtime.
    shutil.make_archive(str(root/'dist'/'TikTokVideoMaker_Portable'),'zip',root/'dist','TikTokVideoMaker')

if __name__=='__main__':main()
