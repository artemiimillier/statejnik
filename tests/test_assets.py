"""Offline end-to-end delivery: move the export without its source folder."""
import base64
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/statejnik/scripts/publish.py'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


class AssetExport(unittest.TestCase):
    def test_asset_boundaries(self):
        sys.path.insert(0, str(SCRIPT.parent))
        from _assets import local_assets, export_assets
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'assets').mkdir()
            (root / 'private.txt').write_text('not public')
            (root / 'assets/public.txt').write_text('public')
            for url in ['assets/../private.txt', 'assets/missing.png', '/private.png', 'assets/public.txt?x=1']:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    local_assets('![Image](' + url + ')', root / 'final.md')
            (root / 'assets/link.txt').symlink_to(root / 'private.txt')
            with self.assertRaises(ValueError):
                local_assets('[File](assets/link.txt)', root / 'final.md')
            (root / 'out').mkdir()
            (root / 'out/sample-assets').symlink_to(root / 'assets', target_is_directory=True)
            with self.assertRaises(ValueError):
                export_assets('[File](assets/public.txt)', root / 'final.md', root / 'out', 'sample')

    def test_remote_adapter_refuses_local_assets_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'assets').mkdir()
            (root / 'assets/image.png').write_bytes(PNG)
            (root / 'final.md').write_text('# Test\n\n![Image](assets/image.png)\n')
            (root / 'statejnik.yaml').write_text('publish:\n  targets:\n    remote:\n      type: wordpress\n')
            result = subprocess.run([sys.executable, str(SCRIPT), 'send', 'remote', 'final.md'], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('does not upload assets', result.stderr)
            self.assertFalse((root / 'work/published.json').exists())

    def test_export_survives_source_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / 'work/sample'
            (article / 'assets').mkdir(parents=True)
            (article / 'assets/chart.png').write_bytes(PNG)
            (article / 'assets/guide.txt').write_text('Public checklist', encoding='utf-8')
            (article / 'final.md').write_text('---\ntitle: Sample\nslug: sample\n---\n\n![Chart](assets/chart.png)\n\n[Checklist](assets/guide.txt)\n', encoding='utf-8')
            (root / 'statejnik.yaml').write_text('publish:\n  targets:\n    files:\n      type: files\n      dir: out\n', encoding='utf-8')
            result = subprocess.run([sys.executable, str(SCRIPT), 'send', 'files', 'work/sample/final.md', '--status', 'draft'], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            shutil.rmtree(article)
            shutil.move(root / 'out', root / 'moved')
            exported = root / 'moved'
            text = (exported / 'sample.md').read_text(encoding='utf-8')
            self.assertIn('sample-assets/chart.png', text)
            self.assertIn('sample-assets/guide.txt', text)
            self.assertEqual((exported / 'sample-assets/chart.png').read_bytes(), PNG)
            self.assertEqual((exported / 'sample-assets/guide.txt').read_text(), 'Public checklist')
            rendered = (exported / 'sample.html').read_text(encoding='utf-8')
            self.assertIn('src="sample-assets/chart.png"', rendered)
            self.assertIn('href="sample-assets/guide.txt"', rendered)
            self.assertIn('width=device-width', rendered)
            files = json.loads((root / 'work/published.json').read_text())['sample']['files']['files']
            self.assertIn('out/sample-assets/chart.png', files)


if __name__ == '__main__':
    unittest.main()
