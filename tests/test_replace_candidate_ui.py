"""Small archive checks; no application build or Windows run is performed here."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from replace_candidate_ui import BASE_COMMIT, PREFIX, PROVENANCE, UI, digest_file, replace_ui


class ReplaceCandidateUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.original = self.root / 'original.zip'
        self.output = self.root / 'dist' / 'TikTokVideoMaker_Portable.zip'
        self.resource = self.root / 'connected.js'
        self.resource.write_bytes(b'new UI')
        self.evidence = self.root / 'evidence.json'
        self.members = {
            UI: b'old UI', PREFIX + 'TikTokVideoMaker.exe': b'MZ old EXE',
            PREFIX + 'build-manifest.json': json.dumps({'source_commit': BASE_COMMIT}).encode(),
            PREFIX + 'runtime/ffmpeg.exe': b'MZ original ffmpeg',
            PREFIX + 'runtime/ffprobe.exe': b'MZ original ffprobe',
            PREFIX + 'runtime/webview2/msedgewebview2.exe': b'MZ original webview',
            PREFIX + 'ui/styles.css': b'original CSS', PREFIX + 'ui/': b'',
        }
        with zipfile.ZipFile(self.original, 'w') as bundle:
            for name, content in self.members.items():
                bundle.writestr(name, content)
        self.base_sha = digest_file(self.original)
        self.original.with_suffix('.sha256').write_text(self.base_sha)

    def replace(self):
        return replace_ui(self.original, self.resource, self.output, self.evidence,
                          'b' * 40, base_sha=self.base_sha)

    def test_only_ui_and_new_provenance_change(self):
        result = self.replace()
        self.assertEqual(digest_file(self.original), self.base_sha)
        self.assertEqual(self.output.with_suffix('.sha256').read_text(), digest_file(self.output))
        with zipfile.ZipFile(self.output) as bundle:
            self.assertEqual(set(bundle.namelist()), set(self.members) | {PROVENANCE})
            for name, content in self.members.items():
                self.assertEqual(bundle.read(name), b'new UI' if name == UI else content)
            provenance = json.loads(bundle.read(PROVENANCE))
            self.assertFalse(provenance['exe_rebuilt'])
            self.assertEqual(provenance['original_exe_build_commit'], BASE_COMMIT)
        self.assertEqual(len(result['members']), len(self.members) + 1)
        self.assertTrue(result['all_other_members_verified'])
        self.assertEqual(json.loads(self.evidence.read_text()), result)

    def test_wrong_base_hash_fails_before_candidate_creation(self):
        self.base_sha = '0' * 64
        with self.assertRaisesRegex(ValueError, 'candidate SHA-256'):
            self.replace()
        self.assertFalse(self.output.exists())

    def test_wrong_sidecar_fails_before_candidate_creation(self):
        self.original.with_suffix('.sha256').write_text('0' * 64)
        with self.assertRaisesRegex(ValueError, 'sidecar'):
            self.replace()
        self.assertFalse(self.output.exists())

    def test_no_change_does_not_create_candidate(self):
        self.resource.write_bytes(b'old UI')
        with self.assertRaisesRegex(ValueError, 'No UI change'):
            self.replace()
        self.assertFalse(self.output.exists())

    def test_existing_candidate_is_never_overwritten(self):
        self.output.parent.mkdir()
        self.output.write_bytes(b'preserved candidate')
        with self.assertRaises(FileExistsError):
            self.replace()
        self.assertEqual(self.output.read_bytes(), b'preserved candidate')


if __name__ == '__main__':
    unittest.main()
