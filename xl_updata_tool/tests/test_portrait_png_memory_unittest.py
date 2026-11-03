import unittest
import os
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
class NativePngRegression(unittest.TestCase):
    def test_native_rgba_and_hdr_match_baseline(self):
        directory = os.environ.get('XL_PNG_MEMORY_EVIDENCE')
        if not directory:
            self.skipTest('set XL_PNG_MEMORY_EVIDENCE to native before/after evidence directory')
        from PIL import Image
        directory = Path(directory)
        with Image.open(directory/'baseline.png') as before, Image.open(directory/'optimized.png') as after:
            self.assertEqual(before.size, after.size)
            self.assertEqual(before.convert('RGBA').tobytes(), after.convert('RGBA').tobytes())
        before = json.loads((directory/'baseline-complete.json').read_text())
        after = json.loads((directory/'optimized-complete.json').read_text())
        for key in ('width', 'height', 'float_peak', 'bounds', 'frames', 'snapshot_time'):
            self.assertEqual(before[key], after[key], key)
        self.assertGreater(before['float_peak'], 1, 'fixture must exercise HDR accumulation')
if __name__ == '__main__':
    unittest.main()
