import importlib.util
import unittest
from pathlib import Path

from server_app.portraits.executor import Deferred


class PackageProtocolTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('server_app.portraits.package_protocol'))
        from server_app.portraits import package_protocol
        return package_protocol
    def test_cancel_is_boolean_and_first_typed_reason_survives_recovery(self):
        module=self.module()
        calls=[]
        reason=Deferred('updater')
        def guard():
            calls.append(1)
            if len(calls)==1:raise reason
        latch=module.StopLatch(guard)
        self.assertIs(latch(),True)
        self.assertIs(latch(),True)
        with self.assertRaises(Deferred) as got:latch.raise_if_stopped()
        self.assertIs(got.exception,reason)
    def test_file_reference_rejects_alias_and_wrong_hash(self):
        import hashlib
        import os
        import tempfile
        module=self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'a').write_bytes(b'abc')
            ref={'relative_path': 'a','size': 3,'sha256': hashlib.sha256(b'abc').hexdigest()}
            self.assertEqual(module.read_bound_file(root,ref,cap=10),b'abc')
            with self.assertRaises(ValueError):module.read_bound_file(root,{**ref,'relative_path':'../a'},cap=10)
            with self.assertRaises(ValueError):module.read_bound_file(root,{**ref,'sha256':'0'*64},cap=10)
            os.link(root/'a',root/'alias')
            with self.assertRaises(ValueError):module.read_bound_file(root,ref,cap=10)
    def test_selection_rebuilds_captured_arts_closure_and_rejects_injection(self):
        import copy

        from test_portrait_input_unittest import InputTests, arts
        module=self.module()
        self.assertTrue(hasattr(module, 'select_input'))
        document=InputTests().build()
        selected=document['source_snapshot']['skins'][0]
        self.assertEqual(module.select_input(document,selected['lazy_input'],arts()),selected)
        for key in selected['lazy_input']:
            bad=dict(selected['lazy_input']);bad[key]='wrong'
            with self.assertRaises(ValueError):module.select_input(document,bad,arts())
        forged=copy.deepcopy(document)
        forged['source_snapshot']['skins'][0]['candidate_bundles'][0]['wire_md5']='d'*32
        with self.assertRaises(ValueError):module.select_input(forged,selected['lazy_input'],arts())
    def test_publisher_cannot_swallow_latched_stop(self):
        import tempfile
        from pathlib import Path

        from app.features.preview.portrait_package import publish_portrait_packages
        module=self.module()
        reason=Deferred('updater changed')
        def guard():raise reason
        latch=module.StopLatch(guard)
        with tempfile.TemporaryDirectory() as tmp:
            summary=publish_portrait_packages((object(),),None,Path(tmp),Path(tmp),cancel_check=latch)
        self.assertTrue(summary.cancelled)
        self.assertEqual(summary.published,0)
        with self.assertRaises(Deferred) as got:latch.raise_if_stopped()
        self.assertIs(got.exception,reason)
if __name__=='__main__':unittest.main()
