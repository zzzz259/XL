"""Real metadata publication-to-store integration; requires SourceIO filesystem support.

Run separately from the portable SQLite-only store suite on the controlled Linux
unit slot (or a Windows environment supporting the producer's fsync/ACL contract).
No bundles, renderer, network, or source preparation workers are involved.
"""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

from server_app.portraits.model import digest
from server_app.portraits.publication import Publication
from server_app.portraits.reader import PortraitReader
from server_app.portraits.source_io import store_metadata
from server_app.portraits.source_prepare import PreparedSourceRef
from server_app.portraits.store import PortraitStore
from test_portrait_store_v2_unittest import plan


class PublicationStoreTests(unittest.TestCase):
    def test_actual_publication_complete_register_reader_roundtrip(self):
        with tempfile.TemporaryDirectory() as area:
            base=Path(area)
            pub=Publication(base,instance_id='i',source_id='s',cdn_base='https://fixture.invalid/Android/Bundles')
            raw=json.dumps({'timestamp':123,'data':[
                {'name':name,'hash':char*32,'size':2,'ver':1}
                for name,char in [('Data','a'),('Arts','b')]]}).encode()
            pin=pub.capture(raw,version=123,version_file='original.json')
            snapshot={'version':123,'characters':{'1':{'name':'fixture'}},'index':[],'words':{}}
            for name,value in [('character_data/current.json',snapshot),
                               ('current_version.json',{'version':123}),
                               ('versions/123/character_data/current.json',snapshot),
                               ('versions/123/manifest.json',{'version':123,'baseline':True})]:
                path=base/name; path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps(value),encoding='utf-8')
            ready=pub.complete(123)
            request=pub.read_request(ready)
            self.assertEqual(request['pin']['pin_digest'],pin.pin_digest)
            self.assertNotEqual(ready.scope_key,pin.scope_key)
            self.assertEqual(ready.scope_key,pub.scope)
            # Genuine MetadataRef bytes for a synthetic capture. No claim of
            # SourcePrepare authority: this test exercises the reference bridge.
            snapshot_digest=digest({'fixture':'nested input snapshot'})
            capture=store_metadata(json.dumps({'snapshot_digest':snapshot_digest}).encode(),
                                   data_root=base/'captures',scope_key=pin.scope_key,pin_digest=pin.pin_digest)
            prepared=PreparedSourceRef('portrait-prepared-v1','i','s','123',pin.pin_digest,'c'*64,
                                       capture,snapshot_digest,0,0,0,0)
            self.assertNotEqual(capture.sha256,snapshot_digest)
            p=plan(True); p.update(version='123',input_snapshot_digest=snapshot_digest,
                                  source_capture_ref=asdict(prepared),core_publication_ref=asdict(ready))
            store=PortraitStore(base/'jobs.db','i','s')
            registered=store.register_closed_plan(p)
            self.assertEqual(store.register_closed_plan(p)['inserted'],0)
            reader=PortraitReader(base/'jobs.db','i','s')
            frozen=reader.read_manifest(registered['plan_id'])
            self.assertEqual(frozen['core_publication_ref'],asdict(ready))
            self.assertEqual(frozen['source_capture_ref'],asdict(prepared))
            self.assertEqual(store.seal_plan(registered['plan_id']),reader.read_plan_snapshot(registered['plan_id']))


if __name__=='__main__': unittest.main()
