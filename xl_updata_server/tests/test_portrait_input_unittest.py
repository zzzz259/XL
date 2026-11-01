"""Synthetic metadata only: no Unity/native/network/filesystem dependencies."""
import copy
import importlib.util
import unittest
from unittest.mock import patch
from server_app.portraits import plan_changes
from server_app.portraits.model import digest


def root(s='10', c='1', **changes):
    return dict(skin_id=s,character_id=c,prefab_name='cardspine_a.prefab',position=[0,1,2],entry_id=s,**changes)


def arts():
    return dict(dirs=['assets/portraits'],bundles=[
        dict(name='root',hash='a'*32,size=10,deps=[1]),
        dict(name='shared',hash='b'*32,size=20,deps=[0]),
        dict(name='other',hash='c'*32,size=30,deps=[])],assets=[
        dict(name='cardspine_a.prefab',dir=0,bundle=0,deps=[1]),
        dict(name='shader.shader',dir=0,bundle=1,deps=[])])


def manifest(v='1'):
    return dict(instance_id='i',source_id='s',version=v,version_file='versions/1',version_sha256='1'*64,
                categories={k:dict(name=k,wire_md5='2'*32,size=100,sha256='3'*64,locator=k+'.json') for k in ('Data','Arts')},
                config=dict(wire_md5='4'*32,decoded_sha256='5'*64,decoder='decoder-release-sha256:'+'6'*64,locator='decoded/BaseFashion.lua'))


class InputTests(unittest.TestCase):
    def build(self, roots=None, catalog=None, version='1', **changes):
        spec=importlib.util.find_spec('server_app.portraits.input')
        self.assertIsNotNone(spec, 'metadata input builder is missing')
        from server_app.portraits.input import build_input_snapshot
        args=dict(version=version,instance_id='i',source_id='s',source_manifest=manifest(version),tool_fingerprint='f'*64)
        args.update(changes)
        return build_input_snapshot(roots if roots is not None else [root()],catalog if catalog is not None else arts(),**args)

    def test_baseline_ordinals_orphan_and_exclusions(self):
        missing=root('2'); missing['prefab_name']=None
        orphan=root('30',None); excluded=root('40',family='battlespine')
        rows=[root('20'),missing,orphan,excluded,root('50','2')]
        snapshot=self.build(rows); catalog=snapshot['catalog']
        self.assertEqual([(r['character_id'],r['skin_id'],r['ordinal'],r['state']) for r in catalog['skins']],
                         [('1','2',1,'blocked'),('1','20',2,'not_generated'),('2','50',1,'not_generated')])
        self.assertEqual(plan_changes(None,catalog),[])
        self.assertIn('missing_character_id',snapshot['source_snapshot']['unassigned_roots'][0]['diagnostics'])
        self.assertNotIn('package_path',str(snapshot))

    def test_copy_safe_version_and_evidence_are_not_render_inputs(self):
        rows=[root()]; catalog=arts(); source=manifest(); before=copy.deepcopy((rows,catalog,source))
        a=self.build(rows,catalog,source_manifest=source)
        self.assertEqual((rows,catalog,source),before)
        changed=root(evidence='elsewhere:20',sort=999,shop=True); changed['position']=[0.0,1.0,2.0]
        b=self.build([changed],version='2',character_names={'1':'renamed'})
        self.assertEqual(plan_changes(a['catalog'],b['catalog']),[])
        rows[0]['position'][0]=33; source['config']['decoder']='mutated'
        self.assertEqual(a['source_snapshot']['skins'][0]['root']['position'],[0,1,2])
        self.assertEqual(a,self.build())

    def test_catalog_reordering_and_cycles_are_stable(self):
        a=self.build(); b=arts(); b['bundles']=[b['bundles'][2],b['bundles'][1],b['bundles'][0]]
        b['bundles'][1]['deps']=[2]; b['bundles'][2]['deps']=[1]
        b['assets'].reverse(); b['assets'][0]['bundle']=1; b['assets'][1]['bundle']=2; b['assets'][1]['deps']=[0]
        self.assertEqual(a,self.build(catalog=b))

    def test_two_coordinate_positions_are_valid(self):
        r=root(); r['position']=[383,-1100]
        snapshot=self.build([r])
        self.assertEqual(snapshot['source_snapshot']['skins'][0]['root']['position'],[383,-1100])
        self.assertEqual(snapshot['catalog']['skins'][0]['state'],'not_generated')

    def test_snapshot_digest_and_root_order(self):
        a=self.build([root('20'),root('10')]); b=self.build([root('10'),root('20')])
        self.assertEqual(a,b)
        source=copy.deepcopy(a['source_snapshot']); claimed=source.pop('snapshot_digest')
        self.assertEqual(claimed,digest(source))

    def test_optional_nested_scope_cannot_disagree(self):
        for nested in ('config','Arts','Data'):
            m=manifest(); row=m['config'] if nested=='config' else m['categories'][nested]
            row['version']='another-version'
            with self.subTest(nested=nested),self.assertRaises(ValueError): self.build(source_manifest=m)

    def test_budget_limits_and_position_range(self):
        self.build()  # Load builder before patching its public budget constants.
        with patch('server_app.portraits.input.MAX_ASSETS',1),self.assertRaises(ValueError): self.build()
        with patch('server_app.portraits.input.MAX_CANDIDATE_ITEMS',1),self.assertRaises(ValueError): self.build()
        r=root(); r['position']=[10**400,0]
        with self.assertRaises(ValueError): self.build([r])

    def test_asset_cycles_and_reachable_external_bundle_hash(self):
        b=arts(); b['assets'][1]['deps']=[0]; b['assets'][1]['bundle']=2
        a=self.build(catalog=b); b['bundles'][2]['hash']='d'*32
        self.assertEqual(len(plan_changes(a['catalog'],self.build(catalog=b)['catalog'])),1)

    def test_reachable_asset_edges_change_fingerprint_with_identical_bundles(self):
        b=arts(); b['assets'].append(dict(name='second.shader',dir=0,bundle=1,deps=[]))
        b['assets'][0]['deps']=[1,2]
        a=self.build(catalog=b)
        b['assets'][0]['deps']=[1]; b['assets'][1]['deps']=[2]
        changed=self.build(catalog=b)
        self.assertEqual(a['source_snapshot']['skins'][0]['candidate_bundles'],changed['source_snapshot']['skins'][0]['candidate_bundles'])
        self.assertNotEqual(a['catalog']['skins'][0]['dependency_fingerprint'],changed['catalog']['skins'][0]['dependency_fingerprint'])
        self.assertEqual(len(plan_changes(a['catalog'],changed['catalog'])),1)
        self.assertEqual(changed['source_snapshot']['skins'][0]['asset_edges'],[
            {'source':{'full_path':'assets/portraits/cardspine_a.prefab','bundle_name':'root'},
             'target':{'full_path':'assets/portraits/shader.shader','bundle_name':'shared'}},
            {'source':{'full_path':'assets/portraits/shader.shader','bundle_name':'shared'},
             'target':{'full_path':'assets/portraits/second.shader','bundle_name':'shared'}}])

    def test_exact_catalog_case_preserved_with_casefold_lookup(self):
        b=arts(); b['dirs']=['Assets/Portraits']; b['assets'][0]['name']='CardSpine_A.prefab'
        b['bundles'][0]['name']='Bundles/ROOT'
        source=self.build(catalog=b)['source_snapshot']['skins'][0]
        self.assertEqual(source['matched_assets'],[{'full_path':'Assets/Portraits/CardSpine_A.prefab','bundle_name':'Bundles/ROOT'}])
        self.assertEqual(source['candidate_bundles'][0]['name'],'Bundles/ROOT')
        self.assertIn(['Bundles/ROOT','shared'],source['bundle_edges'])
        self.assertNotEqual(self.build()['catalog']['skins'][0]['dependency_fingerprint'],source['dependency_fingerprint'])
        r=root(); r['prefab_name']='ASSETS/PORTRAITS/CARDSPINE_A.PREFAB'
        self.assertEqual(self.build([r],b)['catalog']['skins'][0]['state'],'not_generated')
        b['assets'].append(dict(name='cardspine_a.prefab',dir=0,bundle=2,deps=[]))
        self.assertEqual(self.build([r],b)['catalog']['skins'][0]['state'],'blocked')

    def test_duplicate_stable_asset_identity_is_rejected(self):
        b=arts(); b['assets'].append(copy.deepcopy(b['assets'][1]))
        with self.assertRaises(ValueError): self.build(catalog=b)

    def test_equal_skin_unassigned_records_have_total_order(self):
        a=root('30',None,evidence='first:10'); b=root('30',None,evidence='other:20'); b['entry_id']='31'
        self.assertEqual(self.build([a,b]),self.build([b,a]))

    def test_targeted_bundle_and_config_changes(self):
        a=self.build(); b=arts(); b['bundles'][2]['hash']='d'*32
        self.assertEqual(plan_changes(a['catalog'],self.build(catalog=b)['catalog']),[])
        for change in ('hash','topology','position','missing'):
            b=arts(); r=root()
            if change=='hash': b['bundles'][1]['hash']='d'*32
            elif change=='topology': b['bundles'][1]['deps']=[0,2]
            elif change=='position': r['position']=[1,2,3]
            else: b['assets']=[]
            with self.subTest(change=change):
                self.assertEqual(len(plan_changes(a['catalog'],self.build([r],b)['catalog'])),1)
        empty=self.build(catalog=dict(dirs=[],assets=[],bundles=[]))
        self.assertEqual(len(plan_changes(empty['catalog'],a['catalog'])),1)

    def test_ambiguity_full_path_and_asset_dependency_outside_bundle_graph(self):
        b=arts(); b['dirs'].append('other'); b['assets'].append(dict(name='cardspine_a.prefab',dir=1,bundle=2,deps=[]))
        self.assertEqual(self.build(catalog=b)['catalog']['skins'][0]['state'],'blocked')
        r=root(); r['prefab_name']='assets/portraits/cardspine_a.prefab'
        self.assertEqual(self.build([r],b)['catalog']['skins'][0]['state'],'not_generated')
        b=arts(); b['assets'][1]['bundle']=2
        skin=self.build(catalog=b)['source_snapshot']['skins'][0]
        self.assertEqual([n['name'] for n in skin['candidate_bundles']],['other','root','shared'])

    def test_invalid_source_and_selected_metadata_rejected(self):
        cases=[]
        for field,value in [('hash','bad'),('size',True),('deps',[-1]),('deps',[True]),('name','../bad')]:
            b=arts(); b['bundles'][0][field]=value; cases.append(b)
        for field,value in [('dir',-1),('bundle',True),('deps',[99]),('name','../bad')]:
            b=arts(); b['assets'][0][field]=value; cases.append(b)
        b=arts(); b['bundles'][2]['name']='root'; cases.append(b)
        for b in cases:
            with self.subTest(catalog=b),self.assertRaises(ValueError): self.build(catalog=b)
        for field,value in [('skin_id','01'),('character_id',True),('position',[True,0,0]),('position',[float('nan'),0,0]),('position',[1]),('version','other')]:
            r=root(); r[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError): self.build([r])
        for field in ('instance_id','source_id','version','version_sha256'):
            m=manifest(); m[field]='wrong'
            with self.subTest(field=field),self.assertRaises(ValueError): self.build(source_manifest=m)

    def test_bounds_and_json_cycles(self):
        m=manifest(); nested=m
        for _ in range(40): nested['extra']={}; nested=nested['extra']
        with self.assertRaises(ValueError): self.build(source_manifest=m)
        m=manifest(); m['cycle']=m
        with self.assertRaises(ValueError): self.build(source_manifest=m)
        with self.assertRaises(ValueError): self.build([root()]*10001)


if __name__=='__main__': unittest.main()
