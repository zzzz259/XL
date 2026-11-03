import importlib.util
import unittest


class PackagePrepareTests(unittest.TestCase):
    def test_invalid_slot_refused_before_authority_or_imports(self):
        self.assertIsNotNone(importlib.util.find_spec('server_app.portraits.package_prepare'))
        from server_app.portraits.executor import BoundaryError
        from server_app.portraits.package_prepare import prepare_lazy_package
        with self.assertRaises(BoundaryError):  # noqa: SIM117 - explicit context entry failure
            with prepare_lazy_package(None,None,None,None,publication=None,data_root=None,
                    work_root=None,runtime=None,package_runtime=None,open_remote=None,stop_event=None):
                self.fail('invalid slot accepted')
    def test_poisoned_or_active_cleanup_retains_attempt(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace

        from server_app.portraits import package_prepare
        from server_app.portraits.executor import CleanupError
        self.assertTrue(hasattr(package_prepare,'_cleanup_attempt'))
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'owned';path.mkdir();(path/'input').write_bytes(b'held')
            for poisoned,process,fd in ((True,None,3),(False,object(),3),(False,None,None)):
                slot=SimpleNamespace(poisoned=poisoned,process=process,fd=fd,_require_owner=lambda:None)
                with self.assertRaises(CleanupError):package_prepare._cleanup_attempt(slot,path)
                self.assertTrue((path/'input').exists())
            slot=SimpleNamespace(poisoned=False,process=None,fd=3,_require_owner=lambda:None)
            package_prepare._cleanup_attempt(slot,path)
            self.assertFalse(path.exists())
if __name__=='__main__':unittest.main()
