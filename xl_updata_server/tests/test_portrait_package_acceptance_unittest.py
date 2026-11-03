import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path


def sample():
    # Producer-shaped miniature scene; package bytes are independently fixed, not made by validators.
    from app.features.preview.portrait_config.model import (
        AssetRef,
        PortraitScene,
        ScenePart,
        SpineState,
    )
    wire='a'*32; ref=lambda n:AssetRef(wire,n)
    scene=PortraitScene(game_version='',prefab=None,source_bundles=(wire,),version_timestamp='1',entry_config_id='2',character_id='1',skin_id='2',
        parts=(ScenePart('spine',ref(9),'root',spine=SpineState(ref(8),skeleton_json=ref(1),atlas=ref(2),texture=ref(3))),)).to_dict()
    data={1:b'skeleton',2:b'image.png\nsize: 1,1\n',3:b'image bytes'}
    directory='part_0000_'+hashlib.sha256(b'spine').hexdigest()[:12]
    ledger=[];inputs={};resources=[];files={}
    for n,name,kind in ((1,'a.skel','TextAsset'),(2,'a.atlas','TextAsset'),(3,'image','Texture2D')):
        filename=name+'.png' if kind=='Texture2D' else name
        sha=hashlib.sha256(data[n]).hexdigest();path=directory+'/'+filename
        source={'bundle_hash':wire,'path_id':n}
        trace={'source': source,'source_sha256': sha,'source_name': name,'source_container': '','asset_type': kind}
        resources.append(dict(path=path,size=len(data[n]),sha256=sha,**trace))
        inputs[wire+':'+str(n)]=dict(**trace,path=path,paths=[path]);files[path]=data[n]
        ledger.append({'wire_md5': wire,'source_sha256': 'd'*64,'cab': 'CAB-'+'b'*32,'path_id': n,'asset_type': kind,'name': name,
            'size': len(data[n]),'sha256': sha,'output': {'relative_path': f'_portrait_inputs/{wire}/{n}/{filename}','size': len(data[n]),'sha256': sha}})
    part={'part_id': 'spine','directory': directory,'unity_order': [0,0,0.0,0],'kind': 'spine','skeleton_path': directory+'/a.skel',
        'atlas_paths': [directory+'/a.atlas'],'texture_paths': [directory+'/image.png'],'internal_skins': ['default'],'animations': {'idle':0.0},
        'expression_selectors': [],'default_animation': '','loop': False,'time_scale': 1.0,'initial_internal_skin': '','animation_sequence': [],
        'metadata': {'query_ok': True,'internal_skins': ['default'],'animations': {'idle':0.0}}}
    manifest={'package_schema_version': 2,'game_skin_id': '2','scene': scene,'parts': [part],'inputs': inputs,'resources': resources}
    return manifest,ledger,files,data


class ProtectedAcceptanceTests(unittest.TestCase):
    def validator(self):
        from server_app.portraits import package_validation
        self.assertTrue(hasattr(package_validation,'validate_portable_package'))
        return package_validation.validate_portable_package

    def test_exact_inputs_sizes_metadata_and_scene_are_required(self):
        validate=self.validator()
        manifest,ledger,files,data=sample()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);materials=root/'materials';package=root/'package';package.mkdir()
            for path,payload in files.items():
                target=package/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(payload)
            for row in ledger:
                target=materials/row['output']['relative_path'];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data[row['path_id']])
            (package/'skin.json').write_text(json.dumps(manifest))
            validate(manifest,package,scene=manifest['scene'],ledger=ledger,material_root=materials)
            for mutation in ('missing_inputs','wrong_source_sha','wrong_type','wrong_name','wrong_size','false_query',
                             'empty_skins','bool_duration','negative_duration','wrong_scene','wrong_input_paths','extra_resource',
                             'false_query_only','bool_input_id','bool_resource_id','bool_unity_order'):
                with self.subTest(mutation=mutation):
                    bad=copy.deepcopy(manifest);row=next(iter(bad['inputs'].values()));part=bad['parts'][0]
                    if mutation=='missing_inputs':bad['inputs']={}
                    elif mutation=='wrong_source_sha':row['source_sha256']='0'*64
                    elif mutation=='wrong_type':row['asset_type']='Texture2D'
                    elif mutation=='wrong_name':row['source_name']='other'
                    elif mutation=='wrong_size':bad['resources'][0]['size']=1
                    elif mutation=='false_query':part['metadata']={'query_ok':False}
                    elif mutation=='empty_skins':part['metadata']['internal_skins']=[]
                    elif mutation=='bool_duration':part['metadata']['animations']['idle']=True
                    elif mutation=='negative_duration':part['metadata']['animations']['idle']=-1
                    elif mutation=='wrong_scene':bad['scene']['parts'][0]['renderer_depth']=1
                    elif mutation=='wrong_input_paths':row['paths']=[]
                    elif mutation=='false_query_only':part['metadata']['query_ok']=False
                    elif mutation=='bool_input_id':row['source']['path_id']=True
                    elif mutation=='bool_resource_id':bad['resources'][0]['source']['path_id']=True
                    elif mutation=='bool_unity_order':part['unity_order'][0]=False
                    else:bad['resources'].append(copy.deepcopy(bad['resources'][0]))
                    with self.assertRaises(ValueError):validate(bad,package,scene=manifest['scene'],ledger=ledger,material_root=materials)

    def test_ledger_is_bound_to_physical_census_and_identity_path(self):
        from server_app.portraits.package_validation import validate_extraction
        manifest,ledger,_,data=sample();wire='a'*32;cab='CAB-'+'b'*32
        source=lambda n:'normalized/'+'d'*64+'/'+cab+'#'+str(n)
        assets=[{'bundle_hash': wire,'serialized_file': cab,'path_id': n,'name': name,'asset_type': kind,'version_timestamp': 1,'source': source(n)}
                for n,name,kind in [(1,'a.skel','TextAsset'),(2,'a.atlas','TextAsset'),(3,'image','Texture2D'),(8,'metadata','MonoBehaviour'),(9,'root','GameObject'),(10,'bundle','AssetBundle')]]
        manifest['scene'].update(prefab={'bundle_hash':wire,'path_id':9},prefab_container='assets/cardspine_a.prefab')
        catalog={'schema': 'portrait-exact-catalog/v1','version_timestamp': 1,'assets': assets,
            'serialized_files': [{'bundle_hash': wire,'serialized_file': cab,'externals': [],'preload': []}],
            'container_entries': [{'path':'assets/cardspine_a.prefab','owner_bundle_hash':wire,'owner_serialized_file':cab,
                'asset_bundle_path_id':10,'ordinal':0,'asset':{'file_id':0,'path_id':9},'preload_index':0,'preload_size':0}]}
        request={'source': {'version':'1'},'descriptors': [{'wire': {'wire_md5':wire},'sha256': 'd'*64}],
            'selection': {'character_id': '1','skin_id': '2','lazy_input': {'version':'1'},
                'root': {'position':None,'entry_id':'2','prefab_name':'cardspine_a.prefab'},
                'matched_assets':[{'full_path':'assets/cardspine_a.prefab','bundle_name':'a'}],
                'candidate_bundles':[{'name':'a','wire_md5':wire,'size':1}]}}
        with tempfile.TemporaryDirectory() as tmp:
            materials=Path(tmp)
            for row in ledger:
                path=materials/row['output']['relative_path'];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data[row['path_id']])
            validate_extraction(catalog,manifest['scene'],ledger,request,materials)
            for mutation in ('cab','name','asset_type','source_sha256','unknown','output_path','bool_pathid','missing_input','duplicate'):
                with self.subTest(mutation=mutation):
                    bad=copy.deepcopy(ledger)
                    if mutation in ('cab','name','asset_type'):bad[0][mutation]='wrong'
                    elif mutation=='source_sha256':bad[0][mutation]='0'*64
                    elif mutation=='unknown':bad[0]['extra']=1
                    elif mutation=='output_path':bad[0]['output']['relative_path']=bad[1]['output']['relative_path']
                    elif mutation=='bool_pathid':bad[0]['path_id']=True
                    elif mutation=='missing_input':bad.pop()
                    else:bad.append(copy.deepcopy(bad[0]))
                    with self.assertRaises(ValueError):validate_extraction(catalog,manifest['scene'],bad,request,materials)
            for mutation in ('absent_root','wrong_root','wrong_entry','wrong_container','invalid_external','unselected_missing_pointer'):
                with self.subTest(mutation=mutation):
                    bad_scene=copy.deepcopy(manifest['scene']);bad_catalog=copy.deepcopy(catalog)
                    if mutation=='absent_root':bad_scene['prefab']=None
                    elif mutation=='wrong_root':bad_scene['prefab']['path_id']=8
                    elif mutation=='wrong_entry':bad_scene['entry_config_id']='3'
                    elif mutation=='wrong_container':bad_scene['prefab_container']='assets/other.prefab'
                    elif mutation=='invalid_external':bad_catalog['serialized_files'][0]['externals']=['ambient/path']
                    else:bad_catalog['serialized_files'][0]['preload']=[{'file_id':0,'path_id':999}]
                    with self.assertRaises(ValueError):validate_extraction(bad_catalog,bad_scene,ledger,request,materials)

    def test_atlas_rewrite_keeps_original_source_hash(self):
        validate=self.validator();manifest,ledger,files,data=sample()
        # Source CRLF is normalized by publisher, while source_sha256 continues to bind original bytes.
        original=data[2].replace(b'\n',b'\r\n');sha=hashlib.sha256(original).hexdigest()
        row=ledger[1];row.update(size=len(original),sha256=sha);row['output'].update(size=len(original),sha256=sha)
        key='a'*32+':2';manifest['inputs'][key]['source_sha256']=sha
        for resource in manifest['resources']:
            if resource['source']['path_id']==2:resource['source_sha256']=sha
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);package=root/'package';materials=root/'materials';package.mkdir()
            for name,payload in files.items():
                path=package/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(payload)
            for entry in ledger:
                path=materials/entry['output']['relative_path'];path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(original if entry['path_id']==2 else data[entry['path_id']])
            (package/'skin.json').write_text(json.dumps(manifest))
            validate(manifest,package,scene=manifest['scene'],ledger=ledger,material_root=materials)
            bad=copy.deepcopy(manifest);bad['inputs'][key]['source_sha256']=hashlib.sha256(data[2]).hexdigest()
            with self.assertRaises(ValueError):validate(bad,package,scene=manifest['scene'],ledger=ledger,material_root=materials)

    def test_stage_result_counts_must_describe_validated_payload(self):
        from server_app.portraits import package_validation as v
        self.assertTrue(hasattr(v,'validate_package_counts'))
        manifest,_,_,_=sample()
        v.validate_package_counts({'parts':1,'resources':3,'metadata_pairs':1},manifest)
        for key in ('parts','resources','metadata_pairs'):
            bad={'parts':1,'resources':3,'metadata_pairs':1};bad[key]+=1
            with self.assertRaises(ValueError):v.validate_package_counts(bad,manifest)

if __name__=='__main__':unittest.main()
