from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from server_app.portraits import publisher as pub
from server_app.portraits.model import build_catalog, digest, validate_catalog, validate_job
from server_app.portraits.store import PortraitStore
from server_app.portraits.reader import PortraitReader
from test_portrait_store_v2_unittest import plan, result

def catalog(version='v', tool='c'):
    return build_catalog('i','s',version,[dict(character_id='1',skin_id='2',family='cardspine',name='skin',config_fingerprint='a'*64,dependency_fingerprint='a'*64,tool_fingerprint=tool*64)])

def execution(c):
    p=plan(); p['version']=c['version']; p['source_capture_ref']['version']=c['version']
    j=p['jobs'][0]; j['version']=c['version']; j['source_capture_ref']['version']=c['version']; j['render_key']=c['skins'][0]['render_key']
    j['job_id']=digest([j[k] for k in ('instance_id','source_id','version','character_id','skin_id','render_key')]); p['expected_jobset_digest']=digest(p['jobs'])
    return p

class LineageTests(unittest.TestCase):
    def test_registered_job(self):
        j=execution(catalog())['jobs'][0]; self.assertEqual(validate_job(j),j)
    def test_partial_lineage(self):
        c=catalog(); c['skins'][0]['artifact_render_key']='a'*64
        with self.assertRaises(ValueError): validate_catalog(c)
    def test_legacy_unknown(self):
        c=catalog(); c['skins'][0].update(artifact_path='old.png',artifact_sha256='f'*64,source_version='v',state='published')
        self.assertEqual(validate_catalog(c)['skins'][0].get('artifact_lineage'),'legacy_unknown')
    def test_carry(self):
        a=catalog(); r=result(dict(execution(a)['jobs'][0],attempt=1),'success'); r['reason']='new_skin'
        a=pub.project_catalog(a,[r]); b=catalog('B','d'); carried=pub.carry_forward(b,a); row=carried['skins'][0]
        self.assertEqual(row['artifact_render_key'],r['render_key']); self.assertEqual(row['tool_fingerprint'],'d'*64)
        self.assertEqual(row['source_version'],'v'); self.assertIsNone(b['skins'][0]['artifact_path'])
    def test_pure_import(self):
        code="import sys; from server_app.portraits import publisher; assert not any(x.split('.')[0] in ('PIL','numpy','UnityPy','PyQt5','PyQt6','PySide6') for x in sys.modules)"
        run=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True); self.assertEqual(run.returncode,0,run.stderr)

class DurableTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'media'; self.path=Path(self.tmp.name)/'db'; self.store=PortraitStore(self.path,'i','s'); self.a=catalog(); self.b=catalog('B')
    def accepted(self,c=None):
        p=execution(c or self.a); pid=self.store.register_closed_plan(p)['plan_id']; claim=self.store.claim('w',0,10,allowed_plan_ids=[pid]); r=result(claim,'success')
        self.assertTrue(self.store.finish(claim['job_id'],claim['attempt'],r,owner='w',now=1)); return r
    def test_late_samekey(self):
        self.assertTrue(hasattr(pub,'publish_desired'),'missing locked desired API')
        pub.publish_desired(self.a,self.root,expected_current=None); pub.publish_desired(self.b,self.root,expected_current=pub.catalog_token(self.root)); self.accepted()
        page=pub.project_accepted(PortraitReader(self.path,'i','s'),self.root); current=pub.read_catalog(self.root); row=current['skins'][0]
        self.assertEqual(current['version'],'B'); self.assertEqual(row['source_version'],'v'); self.assertEqual(row['artifact_render_key'],row['render_key']); self.assertNotEqual(row['state'],'published')
        before=pub.catalog_token(self.root); pub.project_accepted(PortraitReader(self.path,'i','s'),self.root); self.assertEqual(pub.catalog_token(self.root),before)
        self.assertEqual(len(self.store.list_events()),1); self.assertEqual(len(page['events']),1)
    def test_stale_token_and_candidate(self):
        self.assertTrue(hasattr(pub,'publish_desired'),'missing locked desired API'); pub.publish_desired(self.a,self.root,expected_current=None)
        with self.assertRaises(ValueError): pub.publish_desired(self.b,self.root,expected_current=None)
        with self.assertRaises(TypeError): pub.project_accepted({'accepted':True},self.root)


class AdversarialTests(DurableTests):
    def test_manual_retry_total_order_survives_every_page_permutation(self):
        import itertools
        a,z,b=catalog('A'),catalog('Z'),catalog('B')
        sealed=[]
        for c,request in ((a,None),(z,None),(a,'manual-retry')):
            manifest=execution(c)
            if request: manifest.update(kind='manual',request_id=request)
            pid=self.store.register_closed_plan(manifest)['plan_id']
            claim=self.store.claim('w',0,10,allowed_plan_ids=[pid])
            self.assertTrue(self.store.finish(claim['job_id'],claim['attempt'],result(claim,'success'),owner='w',now=1))
            sealed.append((pid,self.store.seal_plan(pid)))
        reader=PortraitReader(self.path,'i','s')
        events=reader.read_events()['events']
        self.assertEqual([(e['version'],e['attempt']) for e in events],[('A',1),('Z',1),('A',2)])
        for order in itertools.permutations((0,1,2)):
            root=self.root/('order'+''.join(map(str,order)))
            for c in (a,z,b): pub.publish_desired(c,root,expected_current=pub.catalog_token(root))
            for after in order: pub.project_accepted(reader,root,after_id=after,limit=1)
            for after in (1,0,2,0):
                pub.project_accepted(reader,root,after_id=after,limit=1)
                row=pub.read_catalog(root)['skins'][0]
                self.assertEqual((row['source_version'],row['attempt'],row['artifact_event']['id']),('A',2,3),order)
        for pid,snapshot in sealed: self.assertEqual(self.store.seal_plan(pid),snapshot)
        self.assertEqual(len(self.store.list_events()),3)

    def test_generation_republish_and_new_plan_state_are_distinct(self):
        import copy
        original=execution(self.a); pid=self.store.register_closed_plan(original)['plan_id']
        pub.publish_desired(self.a,self.root,expected_current=None,execution_plan_id=pid)
        claim=self.store.claim('w',0,10,allowed_plan_ids=[pid]); self.assertTrue(self.store.finish(claim['job_id'],1,result(claim,'success'),owner='w',now=1)); self.store.seal_plan(pid)
        reader=PortraitReader(self.path,'i','s'); pub.project_accepted(reader,self.root)
        accepted=pub.read_catalog(self.root); self.assertEqual(accepted['skins'][0]['state'],'published')
        for input_catalog in (self.a,accepted):
            pub.publish_desired(input_catalog,self.root,expected_current=pub.catalog_token(self.root),execution_plan_id=pid)
            self.assertEqual(pub.read_catalog(self.root)['desired_generation'],accepted['desired_generation'])
            self.assertEqual(pub.read_catalog(self.root)['skins'][0]['state'],'published')
        pub.project_accepted(reader,self.root)
        manual=copy.deepcopy(original); manual.update(kind='manual',request_id='new-execution')
        next_pid=self.store.register_closed_plan(manual)['plan_id']
        pending=copy.deepcopy(self.a); pending['skins'][0]['state']='pending'
        pub.publish_desired(pending,self.root,expected_current=pub.catalog_token(self.root),execution_plan_id=next_pid)
        pub.project_accepted(reader,self.root) # old success must not finish the new plan
        row=pub.read_catalog(self.root)['skins'][0]
        self.assertEqual(row['state'],'pending'); self.assertEqual(row['plan_id'],pid)
        claim=self.store.claim('w',2,10,allowed_plan_ids=[next_pid]); fail=result(claim); fail['diagnostics']=['new plan failed']
        self.assertTrue(self.store.finish(claim['job_id'],claim['attempt'],fail,owner='w',now=3))
        pub.project_accepted(reader,self.root)
        row=pub.read_catalog(self.root)['skins'][0]
        self.assertEqual(row['state'],'failed'); self.assertEqual(row['diagnostics'],['new plan failed']); self.assertEqual(row['plan_id'],pid)
        pub.publish_desired(pending,self.root,expected_current=pub.catalog_token(self.root),execution_plan_id=next_pid)
        self.assertEqual(pub.read_catalog(self.root)['skins'][0]['state'],'failed')
        # Another new plan cannot be changed by the previous failed plan.
        self.store.seal_plan(next_pid); manual['request_id']='third-execution'; third=self.store.register_closed_plan(manual)['plan_id']
        pub.publish_desired(pending,self.root,expected_current=pub.catalog_token(self.root),execution_plan_id=third)
        pub.project_accepted(reader,self.root)
        self.assertEqual(pub.read_catalog(self.root)['skins'][0]['state'],'pending')

    def test_generation_validation_and_legacy_none_idempotence(self):
        import copy
        pub.publish_desired(self.a,self.root,expected_current=None); self.accepted()
        pub.project_accepted(PortraitReader(self.path,'i','s'),self.root)
        accepted=pub.read_catalog(self.root)
        pub.publish_desired(self.a,self.root,expected_current=pub.catalog_token(self.root))
        self.assertEqual(pub.read_catalog(self.root)['skins'][0]['state'],'published')
        for key,value in [('desired_generation','f'*64),('desired_execution_plan_id','not-a-hash')]:
            bad=copy.deepcopy(accepted); bad[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError): validate_catalog(bad)
        changed=copy.deepcopy(accepted); changed['skins'][0]['name']='new metadata'
        with self.assertRaises(ValueError): validate_catalog(changed)
        # Lifecycle recomputes derived identity; caller's old derived values do not leak.
        pub.publish_desired(changed,self.root,expected_current=pub.catalog_token(self.root))
        fresh=pub.read_catalog(self.root)
        self.assertNotEqual(fresh['desired_generation'],accepted['desired_generation'])
        self.assertEqual(fresh['skins'][0]['state'],'not_generated')
        self.assertIsNotNone(fresh['skins'][0]['artifact_path'])
        with self.assertRaises(ValueError): pub.publish_desired(self.a,self.root,expected_current=pub.catalog_token(self.root),execution_plan_id='bad')

    def test_pure_unattributed_results_do_not_finish_explicit_generation(self):
        pending=catalog(); pending['skins'][0]['state']='pending'
        pub.publish_desired(pending,self.root,expected_current=None,execution_plan_id='e'*64)
        desired=pub.read_catalog(self.root); claim=dict(execution(self.a)['jobs'][0],attempt=1)
        for status in ('success','failed'):
            projected=pub.project_catalog(desired,[result(claim,status)])
            self.assertEqual(projected['skins'][0]['state'],'pending')
            self.assertEqual(projected['skins'][0]['diagnostics'],[])

    def test_owner_terminal_state_is_independent_of_foreign_artifact_rank(self):
        import copy
        for owner_status in ('success','failed'):
            store=PortraitStore(Path(self.tmp.name)/(owner_status+'.db'),'i','s')
            desired=catalog('A'); desired['skins'][0]['state']='pending'
            manifest=execution(desired); p1=store.register_closed_plan(manifest)['plan_id']
            claim=store.claim('w',0,10,allowed_plan_ids=[p1]); owned=result(claim,owner_status); owned['diagnostics']=['owner '+owner_status]
            self.assertTrue(store.finish(claim['job_id'],claim['attempt'],owned,owner='w',now=1)); final1=store.seal_plan(p1)
            manual=copy.deepcopy(manifest); manual.update(kind='manual',request_id='foreign-plan')
            p2=store.register_closed_plan(manual)['plan_id']; claim=store.claim('w',2,10,allowed_plan_ids=[p2]); foreign=result(claim,'success'); foreign['diagnostics']=['foreign success']
            self.assertTrue(store.finish(claim['job_id'],claim['attempt'],foreign,owner='w',now=3)); final2=store.seal_plan(p2)
            reader=PortraitReader(Path(self.tmp.name)/(owner_status+'.db'),'i','s')
            for order in ((0,1),(1,0)):
                root=self.root/(owner_status+str(order[0]))
                pub.publish_desired(desired,root,expected_current=None,execution_plan_id=p1)
                for index,after in enumerate(order):
                    pub.project_accepted(reader,root,after_id=after,limit=1)
                    if index==0 and after==1: self.assertEqual(pub.read_catalog(root)['skins'][0]['state'],'pending')
                expected='published' if owner_status=='success' else 'failed'
                for replay in (False,True):
                    if replay: pub.project_accepted(reader,root)
                    row=pub.read_catalog(root)['skins'][0]
                    self.assertEqual((row['state'],row['diagnostics']),(expected,['owner '+owner_status]),(owner_status,order,replay))
                    self.assertEqual((row['plan_id'],row['attempt'],row['artifact_event']['id']),(p2,2,2))
                token=pub.catalog_token(root); pub.project_accepted(reader,root)
                self.assertEqual(pub.catalog_token(root),token)
            self.assertEqual(store.seal_plan(p1),final1); self.assertEqual(store.seal_plan(p2),final2)
            self.assertEqual(len(store.list_events()),2)

    def test_desired_cannot_introduce_candidate(self):
        r=result(dict(execution(self.a)['jobs'][0],attempt=1),'success')
        forged=pub.project_catalog(self.a,[r])
        pub.publish_desired(forged,self.root,expected_current=None)
        self.assertIsNone(pub.read_catalog(self.root)['skins'][0]['artifact_path'])

    def test_matching_b_wins_and_partition_replay(self):
        pub.publish_desired(self.a,self.root,expected_current=None)
        pub.publish_desired(self.b,self.root,expected_current=pub.catalog_token(self.root))
        self.accepted(self.b); self.accepted(self.a)
        reader=PortraitReader(self.path,'i','s')
        page=pub.project_accepted(reader,self.root,limit=1)
        pub.project_accepted(reader,self.root,limit=1,cursor=page['cursor'])
        self.assertEqual(pub.read_catalog(self.root)['skins'][0]['source_version'],'B')
        token=pub.catalog_token(self.root); pub.project_accepted(reader,self.root)
        self.assertEqual(pub.catalog_token(self.root),token)

    def test_changed_tool_rejects_late_and_preserves_desired(self):
        pub.publish_desired(self.a,self.root,expected_current=None)
        pub.publish_desired(catalog('B','d'),self.root,expected_current=pub.catalog_token(self.root))
        self.accepted(); pub.project_accepted(PortraitReader(self.path,'i','s'),self.root)
        self.assertIsNone(pub.read_catalog(self.root)['skins'][0]['artifact_path'])

    def test_projection_crash_replay_and_current_never_old(self):
        from unittest.mock import patch
        pub.publish_desired(self.a,self.root,expected_current=None)
        pub.publish_desired(self.b,self.root,expected_current=pub.catalog_token(self.root)); self.accepted()
        original=pub._atomic_json
        def fail(root,relative,raw):
            if relative=='current.json': raise OSError('crash')
            return original(root,relative,raw)
        with patch.object(pub,'_atomic_json',side_effect=fail):
            with self.assertRaises(OSError): pub.project_accepted(PortraitReader(self.path,'i','s'),self.root)
        self.assertEqual(pub.read_catalog(self.root)['version'],'B')
        pub.project_accepted(PortraitReader(self.path,'i','s'),self.root)
        self.assertEqual(pub.read_catalog(self.root)['skins'][0]['source_version'],'v')
        self.assertEqual(len(self.store.list_events()),1)

    def test_two_independent_projections_and_desired_keep_both_skins(self):
        import copy
        import threading
        from concurrent.futures import ThreadPoolExecutor
        a=build_catalog('i','s','v',[dict(self.a['skins'][0],skin_id=str(n)) for n in (2,3)])
        b=build_catalog('i','s','B',[dict(r,name='new desired') for r in a['skins']])
        p=execution(a); second=copy.deepcopy(p['jobs'][0]); second.update(skin_id='3',render_key=a['skins'][1]['render_key'])
        second['job_id']=digest([second[k] for k in ('instance_id','source_id','version','character_id','skin_id','render_key')])
        p['jobs'].append(second); p['expected_jobset_digest']=digest(p['jobs'])
        pid=self.store.register_closed_plan(p)['plan_id']
        for _ in range(2):
            claim=self.store.claim('w',0,10,allowed_plan_ids=[pid]); self.assertTrue(self.store.finish(claim['job_id'],claim['attempt'],result(claim,'success'),owner='w',now=1))
        pub.publish_desired(a,self.root,expected_current=None)
        barrier=threading.Barrier(3)
        def project(after):
            reader=PortraitReader(self.path,'i','s'); barrier.wait(timeout=5)
            return pub.project_accepted(reader,self.root,after_id=after,limit=1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            with pub.locked_mutation(self.root) as mutation:
                futures=[pool.submit(project,n) for n in (0,1)]; barrier.wait(timeout=5)
                mutation.publish_desired(b,expected_current=pub.catalog_token(self.root))
            for future in futures: future.result(timeout=15)
        rows=pub.read_catalog(self.root)['skins']
        self.assertEqual([r['source_version'] for r in rows],['v','v'])
        self.assertEqual([r['name'] for r in rows],['new desired','new desired'])

    def test_directory_barrier_failure_replay_heals_cached_revision(self):
        from unittest.mock import patch
        pub.publish_desired(self.a,self.root,expected_current=None); self.accepted()
        token=pub.catalog_token(self.root)
        with patch.object(pub,'_sync_directory',side_effect=OSError('directory barrier')):
            with self.assertRaises(OSError): pub.project_accepted(PortraitReader(self.path,'i','s'),self.root)
        self.assertEqual(pub.catalog_token(self.root),token)
        with patch.object(pub,'_sync_directory',wraps=pub._sync_directory) as synced:
            pub.project_accepted(PortraitReader(self.path,'i','s'),self.root)
            self.assertGreaterEqual(synced.call_count,3)
        self.assertIsNotNone(pub.read_catalog(self.root)['skins'][0]['artifact_path'])

    def test_locked_lifecycle_and_projection_merge(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        pub.publish_desired(self.a,self.root,expected_current=None); self.accepted()
        started=threading.Event()
        def project():
            started.set(); return pub.project_accepted(PortraitReader(self.path,'i','s'),self.root)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pub.locked_mutation(self.root) as mutation:
                future=pool.submit(project); self.assertTrue(started.wait(5))
                mutation.publish_desired(self.b,expected_current=pub.catalog_token(self.root))
            future.result(timeout=15)
        self.assertEqual(pub.read_catalog(self.root)['version'],'B')
        self.assertEqual(pub.read_catalog(self.root)['skins'][0]['source_version'],'v')
        with self.assertRaises(RuntimeError): mutation.publish_desired(self.a,expected_current=None)


class GuardTests(unittest.TestCase):
    def test_matching_carried_artifact_beats_stale_version_fallback(self):
        matching=catalog(); j=dict(execution(matching)['jobs'][0],attempt=1)
        matching=pub.project_catalog(matching,[result(j,'success')])
        stale=pub.carry_forward(catalog('old','d'),matching)
        desired=pub.carry_forward(catalog('new'),matching)
        # A prior version pointer can retain a changed-key artifact.
        stale['skins'][0]['artifact_render_key']='e'*64
        stale['skins'][0]['job_id']=digest(['i','s','v','1','2','e'*64])
        merged=pub.carry_forward(desired,stale)
        self.assertEqual(merged['skins'][0]['artifact_render_key'],desired['skins'][0]['render_key'])

    def test_known_cannot_use_legacy_snapshot_writer(self):
        c=catalog(); r=result(dict(execution(c)['jobs'][0],attempt=1),'success')
        known=pub.project_catalog(c,[r])
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError): pub.publish_catalog(known,root,promote=True)

    def test_partial_capture_and_artifact_identity_reject(self):
        import copy
        c=catalog(); r=result(dict(execution(c)['jobs'][0],attempt=1),'success')
        known=pub.project_catalog(c,[r])
        for key,value in [('artifact_render_key','e'*64),('artifact_tool_fingerprint','f'*64),('attempt',False),('source_capture_ref',{}),('archive_path','wrong')]:
            bad=copy.deepcopy(known); bad['skins'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError): validate_catalog(bad)

    def test_old_failure_does_not_mutate_current_and_removed_row_stays_removed(self):
        c=catalog(); j=dict(execution(c)['jobs'][0],attempt=1)
        b=catalog('B'); fail=result(j)
        self.assertEqual(pub.project_catalog(b,[fail]),b)
        removed=dict(b,skins=[])
        self.assertEqual(pub.project_catalog(removed,[result(j,'success')]),removed)

    def test_same_job_attempt_order_independent(self):
        c=catalog(); j=execution(c)['jobs'][0]
        one=result(dict(j,attempt=1),'success'); two=result(dict(j,attempt=2),'success')
        a=pub.project_catalog(c,[one,two]); b=pub.project_catalog(c,[two,one])
        self.assertEqual(a,b); self.assertEqual(a['skins'][0]['attempt'],2)

if __name__=='__main__': unittest.main()
