"""Download build dependencies from their publishers; never use the runner's browser."""
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent

def fetch(url):
    with urllib.request.urlopen(url, timeout=180) as response:
        return response.read()

def main():
    if sys.platform != 'win32':
        raise SystemExit('Windows required')
    cache = ROOT/'build'/'downloads'
    cache.mkdir(parents=True, exist_ok=True)
    runtime = ROOT/'runtime'
    runtime.mkdir(exist_ok=True)
    base = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
    payload = fetch(base)
    digest = hashlib.sha256(payload).hexdigest()
    expected = fetch(base+'.sha256').decode().split()[0].lower()
    if digest != expected:
        raise RuntimeError('FFmpeg checksum mismatch')
    archive = cache/'ffmpeg.zip'
    archive.write_bytes(payload)
    with zipfile.ZipFile(archive) as bundle:
        for name in ('ffmpeg.exe', 'ffprobe.exe', 'LICENSE'):
            matches = [p for p in bundle.namelist() if p.endswith('/'+name)]
            if len(matches) != 1:
                raise RuntimeError('Missing or ambiguous FFmpeg component: '+name)
            (runtime/name).write_bytes(bundle.read(matches[0]))
    url = os.environ.get('WEBVIEW2_FIXED_URL', '').strip()
    if not url:
        page = html.unescape(fetch('https://developer.microsoft.com/en-us/microsoft-edge/webview2').decode()).replace('\\/', '/')
        links = re.findall(r'https://msedge\.sf\.dl\.delivery\.mp\.microsoft\.com/[^\s"<>]+?\.x64\.cab', page)
        if not links:
            raise RuntimeError('Microsoft page did not expose a fixed x64 CAB URL; supply WEBVIEW2_FIXED_URL from the official download page')
        url = links[0]
    if not re.fullmatch(r'https://msedge\.sf\.dl\.delivery\.mp\.microsoft\.com/[^\s]+\.x64\.cab', url):
        raise ValueError('Only an official Microsoft fixed x64 CAB is accepted')
    cab = cache/'webview2.cab'
    cab.write_bytes(fetch(url))
    unpack = cache/'webview2'
    unpack.mkdir(exist_ok=True)
    subprocess.run(['expand.exe', str(cab), '-F:*', str(unpack)], check=True)
    engines = list(unpack.rglob('msedgewebview2.exe'))
    if len(engines) != 1:
        raise RuntimeError('Expected one fixed WebView2 runtime')
    shutil.copytree(engines[0].parent, runtime/'webview2', dirs_exist_ok=True)
    (runtime/'build-inputs.json').write_text(json.dumps({
        'ffmpeg_url':base, 'ffmpeg_sha256':digest, 'webview2_url':url,
        'webview2_sha256':hashlib.sha256(cab.read_bytes()).hexdigest(),
    }, indent=2), encoding='utf-8')

if __name__ == '__main__':
    main()
