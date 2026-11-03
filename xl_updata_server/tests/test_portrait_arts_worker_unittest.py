import importlib.util
import unittest
from pathlib import Path


def valid_request(source=None, descriptor=None):
    import time

    from server_app.portraits.input import build_input_snapshot
    from test_portrait_input_unittest import manifest
    from test_portrait_source_worker_unittest import request_for
    if source is None:
        source=request_for(Path.cwd().resolve());descriptor=source.pop('descriptor')
    provenance={'character_id': '1','skin_id': '2','entry_id': '2','prefab_name': 'cardspine_a.prefab',
        'position': [0,1,2],'unresolved_reason': None,'version_timestamp': 123,'evidence': 'capture#2'}
    arts={'dirs': ['assets'],'bundles': [{'name': 'a','hash': 'b'*32,'size': 64,'deps': []}],
              'assets': [{'name': 'cardspine_a.prefab','dir': 0,'bundle': 0,'deps': []}]}
    saved=manifest('123');saved.update(instance_id='instance',source_id='source')
    selected=build_input_snapshot([provenance],arts,version='123',instance_id='instance',source_id='source',
        source_manifest=saved,tool_fingerprint='c'*64)['source_snapshot']['skins'][0]
    return {'schema': 'portrait-package-stage/v1','stage': 'arts','source': source,'descriptors': [descriptor],
        'selection': selected,'capture_sha256': 'a'*64,'snapshot_digest': 'b'*64,'tool_fingerprint': 'c'*64,'deadline': time.monotonic()+60}


class ArtsWorkerTests(unittest.TestCase):
    def test_invalid_protocol_rejected_before_heavy_imports(self):
        self.assertIsNotNone(importlib.util.find_spec('server_app.portraits.arts_worker'))
        from server_app.portraits.arts_worker import validate_package_request
        for value in (None, {}, {'schema':'portrait-package-stage/v1','stage':'other'}):
            with self.assertRaises(ValueError):validate_package_request(value)

    def test_full_request_descriptor_membership(self):
        import copy

        from server_app.portraits.arts_worker import validate_package_request
        from test_portrait_source_worker_unittest import request_for
        source=request_for(Path.cwd().resolve())
        descriptor=source.pop('descriptor')
        request = valid_request(source, descriptor)
        self.assertEqual(len(validate_package_request(request)),1)
        for alter in ('duplicate','size','scope','extra'):
            bad=copy.deepcopy(request)
            if alter=='duplicate':bad['descriptors'].append(descriptor)
            elif alter=='size':bad['selection']['candidate_bundles'][0]['size']=65
            elif alter=='scope':bad['selection']['lazy_input']['source_id']='other'
            else:bad['unexpected']=True
            with self.assertRaises(ValueError):validate_package_request(bad)

    def test_stage_entry_rejects_invalid_before_open(self):
        from server_app.portraits import arts_worker
        self.assertTrue(hasattr(arts_worker,'run_request'))
        with self.assertRaises(ValueError):arts_worker.run_request({},request_path=Path('request'),result_path=Path('result'))

    def test_nested_selection_failures_are_rejected_at_protocol_gate(self):
        import copy

        from server_app.portraits.arts_worker import validate_package_request
        for mutation in ('missing_root','missing_provenance','unknown','bool_id','wrong_root_id','wrong_tool',
                         'candidate_extra','root_extra','provenance_extra','lazy_extra','bad_position','bad_field_evidence'):
            with self.subTest(mutation=mutation):
                request=valid_request(); selected=request['selection']
                if mutation=='missing_root':del selected['root']
                elif mutation=='missing_provenance':del selected['provenance']
                elif mutation=='unknown':selected['unexpected']=True
                elif mutation=='bool_id':selected['skin_id']=selected['lazy_input']['skin_id']=True
                elif mutation=='wrong_root_id':selected['root']['skin_id']='3'
                elif mutation=='wrong_tool':selected['tool_fingerprint']='d'*64
                elif mutation=='candidate_extra':selected['candidate_bundles'][0]['url']='https://invalid'
                elif mutation=='root_extra':selected['root']['unknown']=1
                elif mutation=='provenance_extra':selected['provenance']['unknown']=1
                elif mutation=='lazy_extra':selected['lazy_input']['unknown']=1
                elif mutation=='bad_position':selected['root']['position']=[True,0]
                else:selected['provenance']['field_evidence']=[['field',True]]
                with self.assertRaises(ValueError):validate_package_request(copy.deepcopy(request))
                from server_app.portraits.arts_worker import run_request
                with self.assertRaises(ValueError):run_request(request,request_path=Path.cwd()/'request.json',result_path=Path(request['source']['attempt_root'])/'result.json')

    def test_stage_result_schema_is_closed_and_counts_are_typed(self):
        import copy

        from server_app.portraits.package_validation import validate_stage_result
        from server_app.portraits.source_worker import IDENTITY
        request=valid_request()
        output=lambda name:{'relative_path': name+'.json','size': 1,'sha256': 'a'*64}
        result=dict(schema='portrait-package-stage-result/v1',stage='arts',status='ok',
            **{k:request['source'][k] for k in IDENTITY},**{k:request[k] for k in ('capture_sha256','snapshot_digest','tool_fingerprint')},
            lazy_input=request['selection']['lazy_input'],catalog=output('catalog'),scene=output('scene'),ledger=output('ledger'),
            counts={'objects': 1,'entries': 1,'inputs': 1,'parts': 1,'output_bytes': 1,'expanded_bytes': 1,'multiple_memberships': 0,'builtin_edges': 0})
        validate_stage_result(result,request,'arts')
        for mutation in ('extra','missing','count_extra','count_bool','count_limit','ref_extra','wrong_name','wrong_lazy'):
            with self.subTest(mutation=mutation):
                bad=copy.deepcopy(result)
                if mutation=='extra':bad['unknown']=True
                elif mutation=='missing':del bad['ledger']
                elif mutation=='count_extra':bad['counts']['unknown']=1
                elif mutation=='count_bool':bad['counts']['inputs']=True
                elif mutation=='count_limit':bad['counts']['inputs']=129
                elif mutation=='ref_extra':bad['scene']['unknown']=1
                elif mutation=='wrong_name':bad['scene']['relative_path']='other.json'
                else:bad['lazy_input']['skin_id']='3'
                with self.assertRaises(ValueError):validate_stage_result(bad,request,'arts')

if __name__=='__main__':unittest.main()
