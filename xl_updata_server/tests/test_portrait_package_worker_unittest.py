import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class PackageWorkerTests(unittest.TestCase):
    def test_namespace_uses_pinned_posix_shell_and_verifies_copied_bytes(self):
        from server_app.portraits import private_namespace as worker
        self.assertTrue(hasattr(worker,'copy_namespace_tools'))
        sources={'/usr/bin/sh': b'host POSIX shell','/usr/bin/mount': b'host mount'}
        hashes={p:hashlib.sha256(v).hexdigest() for p,v in sources.items()}
        real_sha=worker._sha
        def sha(path):return hashes[str(path)] if str(path) in hashes else real_sha(path)
        def copy(source,target):Path(target).write_bytes(sources[source])
        with tempfile.TemporaryDirectory() as tmp, patch.object(worker,'_sha',side_effect=sha), patch.object(worker.shutil,'copyfile',side_effect=copy):
            shadow=Path(tmp)
            worker.copy_namespace_tools(hashes,shadow)
            self.assertEqual((shadow/'sh').read_bytes(),sources['/usr/bin/sh'])
            self.assertFalse((shadow/'sh').is_symlink())
            self.assertEqual({p.name for p in shadow.iterdir()},{'sh','mount'})
            with self.assertRaises(worker.BoundaryError):worker.copy_namespace_tools({'/usr/bin/dash':hashes['/usr/bin/sh'],'/usr/bin/mount':hashes['/usr/bin/mount']},shadow)
            with patch.object(worker.shutil,'copyfile',side_effect=lambda _,p:Path(p).write_bytes(b'corrupt')), self.assertRaises(worker.BoundaryError):
                worker.copy_namespace_tools(hashes,shadow)

    def test_invalid_request_rejected_before_namespace_or_media_imports(self):
        self.assertIsNotNone(importlib.util.find_spec('server_app.portraits.package_worker'))
        from server_app.portraits.package_worker import run_request
        for request in ({},None,{'stage':'package'}):
            with self.assertRaises(ValueError):run_request(request,request_path=Path('a'),result_path=Path('b'))
if __name__=='__main__':unittest.main()
