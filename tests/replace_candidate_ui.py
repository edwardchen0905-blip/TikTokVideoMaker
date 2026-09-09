"""Replace the verified candidate's external UI resource without rebuilding its EXE."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

BASE_SHA = 'fc2b24ac741334fb199fc883ccb1777236330274fb87015f58e289212ed52b81'
BASE_RUN = '34368614559'
BASE_COMMIT = 'ac3a02748a262da78c82880060cf90f2bc2435be'
PREFIX = 'TikTokVideoMaker/'
UI = PREFIX + 'ui/connected.js'
PROVENANCE = PREFIX + 'ui-update-manifest.json'


def digest_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def replace_ui(archive, resource, output, evidence, source_commit, *, base_sha=BASE_SHA):
    archive, resource, output, evidence = map(Path, (archive, resource, output, evidence))
    if not re.fullmatch('[0-9a-f]{40}', source_commit):
        raise ValueError('A full source commit is required')
    if output.exists() or output.with_suffix('.sha256').exists():
        raise FileExistsError('Refusing to replace an existing candidate or SHA-256')
    if digest_file(archive) != base_sha:
        raise ValueError('Preserved base candidate SHA-256 mismatch')
    if archive.with_suffix('.sha256').read_text(encoding='ascii').strip() != base_sha:
        raise ValueError('Preserved base candidate sidecar SHA-256 mismatch')
    replacement = resource.read_bytes()
    if not replacement:
        raise ValueError('Replacement UI resource is empty')
    expected = {}
    with zipfile.ZipFile(archive) as original:
        names = original.namelist()
        if len(names) != len(set(names)) or PROVENANCE in names:
            raise ValueError('Unexpected duplicate or existing update manifest')
        required = [UI, PREFIX + 'TikTokVideoMaker.exe', PREFIX + 'build-manifest.json',
                    PREFIX + 'runtime/ffmpeg.exe', PREFIX + 'runtime/ffprobe.exe',
                    PREFIX + 'runtime/webview2/msedgewebview2.exe']
        if any(name not in names for name in required):
            raise ValueError('Base candidate is missing a required resource')
        manifest = json.loads(original.read(PREFIX + 'build-manifest.json'))
        if manifest.get('source_commit') != BASE_COMMIT:
            raise ValueError('Unexpected original EXE build commit')
        previous_ui = original.read(UI)
        if previous_ui == replacement:
            raise ValueError('No UI change: refusing an unnecessary replacement ZIP')
        provenance = {
            'base_candidate_sha256': base_sha, 'base_candidate_run_id': BASE_RUN,
            'original_exe_build_commit': BASE_COMMIT, 'ui_source_commit': source_commit,
            'changed_resource': UI, 'old_resource_sha256': hashlib.sha256(previous_ui).hexdigest(),
            'new_resource_sha256': hashlib.sha256(replacement).hexdigest(),
            'exe_rebuilt': False, 'original_build_manifest_unchanged': True,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        evidence.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents this invocation from overwriting a preserved candidate.
        with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as updated:
            for item in original.infolist():
                if item.is_dir():
                    updated.writestr(item, b'')
                    expected[item.filename] = hashlib.sha256(b'').hexdigest()
                    continue
                with original.open(item) as source:
                    if item.filename == UI:
                        content = replacement
                        updated.writestr(item.filename, content)
                        expected[item.filename] = hashlib.sha256(content).hexdigest()
                    else:
                        # Stream large media/browser components; their bytes are not modified.
                        with updated.open(item.filename, 'w') as target:
                            digest = hashlib.sha256()
                            while block := source.read(1024 * 1024):
                                digest.update(block)
                                target.write(block)
                            expected[item.filename] = digest.hexdigest()
            content = (json.dumps(provenance, indent=2) + '\n').encode('utf-8')
            updated.writestr(PROVENANCE, content)
            expected[PROVENANCE] = hashlib.sha256(content).hexdigest()
    members = []
    with zipfile.ZipFile(output) as updated:
        if set(updated.namelist()) != set(expected) or len(updated.namelist()) != len(expected):
            raise ValueError('Replacement candidate member list changed unexpectedly')
        for item in updated.infolist():
            with updated.open(item) as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual != expected[item.filename]:
                raise ValueError('Replacement candidate content mismatch: ' + item.filename)
            members.append({'path': item.filename, 'bytes': item.file_size,
                            'compressed_bytes': item.compress_size, 'sha256': actual})
    if digest_file(archive) != base_sha:
        raise ValueError('Base candidate changed during replacement')
    candidate_sha = digest_file(output)
    record = {**provenance, 'candidate_sha256': candidate_sha,
              'candidate_bytes': output.stat().st_size,
              'uncompressed_bytes': sum(item['bytes'] for item in members),
              'unchanged_original_members': len(expected) - 2,
              'all_other_members_verified': True, 'members': members}
    evidence.write_text(json.dumps(record, indent=2), encoding='utf-8')
    # The acceptance entry point consumes this sidecar only after full member verification.
    output.with_suffix('.sha256').write_text(candidate_sha, encoding='ascii')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit', required=True)
    args = parser.parse_args()
    result = replace_ui('build/base-candidate/TikTokVideoMaker_Portable.zip', 'ui/connected.js',
                        'dist/TikTokVideoMaker_Portable.zip',
                        'build/evidence/candidate-resource-update.json', args.source_commit)
    print(json.dumps({key: value for key, value in result.items() if key != 'members'}, indent=2))
