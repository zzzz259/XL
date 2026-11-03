import unittest
from types import SimpleNamespace as S

from server_app.portraits.bundle_reader import BundleReader, ReaderLimits


class CensusTests(unittest.TestCase):
    def reader(self, *, duplicate=False, bad_pointer=False):
        sf=S(name='CAB-'+'b'*32, externals=[])
        pointer=S(m_FileID=0, m_PathID=True if bad_pointer else 1)
        ab=S(m_PreloadTable=[pointer], m_Container=[('assets/a.prefab',S(asset=pointer,preloadIndex=0,preloadSize=1))])
        obj=S(path_id=1,assets_file=sf,type=S(name='AssetBundle'),byte_size=20,peek_name=lambda:'root',read=lambda:ab)
        objects=[obj,obj] if duplicate else [obj]
        source=S(sha256='c'*64,wire=S(wire_md5='a'*32))
        return BundleReader(S(get_objects=lambda:iter(objects)),source,ReaderLimits())
    def test_census_public_ledger_and_scope_lifetime(self):
        reader=self.reader()
        self.assertTrue(hasattr(reader,'exact_census'))
        result=reader.exact_census(1)
        self.assertEqual(len(result['assets']),1)
        self.assertEqual(result['serialized_files'][0]['preload'],[{'file_id': 0,'path_id': 1}])
        self.assertEqual(result['container_entries'][0]['ordinal'],0)
        reader.close()
        with self.assertRaises(ValueError):reader.exact_census(1)
    def test_duplicate_physical_rejected(self):
        reader=self.reader(duplicate=True)
        self.assertTrue(hasattr(reader,'exact_census'))
        with self.assertRaises(ValueError):reader.exact_census(1)
    def test_bool_pointer_rejected_before_coercion(self):
        reader=self.reader(bad_pointer=True)
        self.assertTrue(hasattr(reader,'exact_census'))
        with self.assertRaises(ValueError):reader.exact_census(1)
    def test_selected_text_preserves_bytes_and_checks_identity(self):
        reader=self.reader()
        obj=reader._bundle.get_objects().__next__()
        obj.type.name='TextAsset'
        obj.read=lambda:S(m_Script='a\udcff')
        self.assertTrue(hasattr(reader,'extract_selected'))
        result=reader.extract_selected(cab='CAB-'+'b'*32,path_id=1,asset_type='TextAsset',name='root')
        self.assertEqual(result['data'],b'a\xff')
        with self.assertRaises(ValueError):
            reader.extract_selected(cab='CAB-'+'b'*32,path_id=1,asset_type='TextAsset',name='wrong')
    def test_texture_pixel_budget_precedes_decode(self):
        reader=self.reader()
        obj=reader._bundle.get_objects().__next__()
        obj.type.name='Texture2D'
        obj.read_typetree=lambda:{'m_Width':8192,'m_Height':8192}
        obj.read=lambda:(_ for _ in ()).throw(AssertionError('decoded oversized texture'))
        self.assertTrue(hasattr(reader,'extract_selected'))
        with self.assertRaisesRegex(ValueError,'pixel'):
            reader.extract_selected(cab='CAB-'+'b'*32,path_id=1,asset_type='Texture2D',name='root')
    def test_streamed_texture_requires_own_spilled_node_and_bounded_range(self):
        import server_app.portraits.bundle_reader as module
        self.assertTrue(hasattr(module, 'validate_texture_stream'))
        cab='CAB-'+'b'*32
        node=S(Length=100)
        tree={'m_StreamData':{'path':'archive:/'+cab+'/'+cab+'.resS','offset':10,'size':20}}
        module.validate_texture_stream(tree,cab,{cab+'.resS':node},64)
        for change in ({'path':'../'+cab+'.resS'},{'offset':90},{'size':65},{'path':'CAB-'+'a'*32+'.resS'}):
            bad={'m_StreamData':{**tree['m_StreamData'],**change}}
            with self.assertRaises(ValueError):module.validate_texture_stream(bad,cab,{cab+'.resS':node},64)
    def test_oversized_metadata_rejected_before_scene_provider_reads(self):
        reader=self.reader()
        obj=reader._bundle.get_objects().__next__()
        obj.byte_size=reader.limits.selected_bytes+1
        with self.assertRaisesRegex(ValueError,'metadata'):
            _ = reader.objects
if __name__=='__main__':unittest.main()
