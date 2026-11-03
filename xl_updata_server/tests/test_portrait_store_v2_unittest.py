"""Closed-plan and read-only contracts, real SQLite only."""
import copy
import tempfile
import unittest
from pathlib import Path
from server_app.portraits.store import PortraitStore
from server_app.portraits.model import PROFILE, digest


def plan(empty=False, manual=None):
    p = dict(schema_version=2, instance_id='i', source_id='s', version='v',
             kind='manual' if manual else 'automatic', input_snapshot_digest='b'*64,
             tool_fingerprint='c'*64, source_capture_ref=dict(schema='portrait-prepared-v1',instance_id='i',source_id='s',version='v',pin_digest='a'*64,tool_fingerprint='c'*64,snapshot_digest='b'*64,capture=dict(scope_key=digest(['i','s']),pin_digest='a'*64,locator='capture.json',size=123,sha256='d'*64),assigned=0,available=0,blocked=0,unassigned=0),
             core_publication_ref=dict(scope_key=digest(['i','s']),pin_digest='a'*64,locator='core.json',size=123,sha256='e'*64),
             desired_catalog_ref={'revision':'r','sha256':'e'*64,'locator':'catalog.json'},
             comparison_evidence={'baseline':empty}, recovery={'max_crashes':2,'deadline':1000,'backoff_seconds':0})
    if manual: p['request_id'] = manual
    j = dict(schema_version=2, instance_id='i', source_id='s', version='v', character_id='1', skin_id='2',
             profile=copy.deepcopy(PROFILE), reason='new_skin', render_key='a'*64,
             input_snapshot_digest=p['input_snapshot_digest'],tool_fingerprint=p['tool_fingerprint'],
             source_capture_ref=copy.deepcopy(p['source_capture_ref']))
    j['job_id'] = digest([j[k] for k in ('instance_id','source_id','version','character_id','skin_id','render_key')])
    p['jobs'] = [] if empty else [j]
    p['expected_jobset_digest'] = digest(p['jobs'])
    return p


def result(c, status='failed'):
    r = {k:copy.deepcopy(c[k]) for k in ('schema_version','instance_id','source_id','version','character_id','skin_id','job_id','render_key','profile','attempt','input_snapshot_digest','tool_fingerprint','source_capture_ref')}
    r.update(status=status,diagnostics=['test'])
    if status=='success':
        r.update(artifact_render_key=c['render_key'],artifact_tool_fingerprint=c['tool_fingerprint'],source_version=c['version'],
                 archive_profile=copy.deepcopy(PROFILE), delivery_profile='original',width=2,height=2,archive_width=2,archive_height=2,
                 artifact_sha256='f'*64,archive_sha256='f'*64,artifact_path='artifacts/'+ 'f'*64+'.png',archive_path='archive/'+ 'f'*64+'.png')
    return r


class ClosedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'q.db'; self.s=PortraitStore(self.path,'i','s')

    def register(self,p=None):
        self.assertTrue(hasattr(self.s,'register_closed_plan'),'missing closed-plan API')
        return self.s.register_closed_plan(p or plan())['plan_id']

    def claim(self,pid,t=0): return self.s.claim('worker',t,10,allowed_plan_ids=[pid])

    def test_zero_restart_seal_and_conflict(self):
        p=plan(True); pid=self.register(p)
        self.assertEqual(self.s.register_closed_plan(p)['inserted'],0)
        self.s=PortraitStore(self.path,'i','s')
        final=self.s.seal_plan(pid)
        self.assertEqual(final['counts'],dict.fromkeys(('success','failed','blocked','cancelled'),0))
        self.assertEqual(final,self.s.seal_plan(pid))
        with self.assertRaises(ValueError): self.s.register_closed_plan(plan())
        with self.assertRaises(KeyError): self.s.seal_plan('a'*64)

    def test_ownership_renew_defer_and_fixed_events(self):
        pid=self.register(); self.assertIsNone(self.s.claim('legacy',0))
        c=self.claim(pid)
        self.assertTrue(self.s.renew(c['job_id'],1,'worker',9,100))
        self.assertIsNone(self.claim(pid,60))
        self.assertFalse(self.s.defer(c['job_id'],1,'other',61,'updater'))
        self.assertTrue(self.s.defer(c['job_id'],1,'worker',61,'updater'))
        self.assertEqual(self.s.list_events(),[]); self.assertIsNone(self.s.seal_plan(pid))
        c2=self.claim(pid,62)
        self.assertEqual(c2['attempt'],2); self.assertEqual(c2['crash_count'],0)
        self.assertFalse(self.s.finish(c['job_id'],1,result(c),owner='worker',now=63))
        self.assertTrue(self.s.finish(c2['job_id'],2,result(c2),owner='worker',now=63))
        final=self.s.seal_plan(pid)
        self.assertFalse(self.s.requeue(c2['job_id']))
        manual=self.register(plan(manual='retry'))
        c3=self.claim(manual,64)
        self.assertTrue(self.s.finish(c3['job_id'],3,result(c3,'success'),owner='worker',now=65))
        self.assertEqual(self.s.seal_plan(pid),final)
        self.assertEqual([e['plan_id'] for e in self.s.list_events()],[pid,manual])

    def test_overlap_and_result_validation(self):
        pid=self.register()
        with self.assertRaises(ValueError): self.register(plan(manual='overlap'))
        c=self.claim(pid)
        for key,value in [('job_id','a'*64),('tool_fingerprint','a'*64),('source_capture_ref',{}),('artifact_render_key','b'*64),('width',0)]:
            r=result(c,'success'); r[key]=value
            with self.assertRaises(ValueError): self.s.finish(c['job_id'],1,r,owner='worker',now=1)
        self.assertEqual(self.s.list_events(),[])
        self.assertFalse(self.s.renew(c['job_id'],1,'worker',10,60))
        self.assertFalse(self.s.finish(c['job_id'],1,result(c),owner='worker',now=10))

    def test_expiry_budget_requires_truthful_terminal(self):
        pid=self.register(); c=self.claim(pid); c=self.claim(pid,10); c=self.claim(pid,20)
        self.assertEqual(c['crash_count'],2); self.assertTrue(c['recovery_required'])
        with self.assertRaises(ValueError): self.s.finish(c['job_id'],c['attempt'],result(c,'success'),owner='worker',now=21)
        self.assertFalse(self.s.defer(c['job_id'],c['attempt'],'worker',21,'updater'))
        r=result(c); r['failure_class']='crash_budget_exhausted'
        self.assertTrue(self.s.finish(c['job_id'],c['attempt'],r,owner='worker',now=21))
        self.assertIsNone(self.claim(pid,100))

    def test_reader_missing_wal_pages_identity(self):
        self.register(plan(True))
        from server_app.portraits.reader import PortraitReader
        missing=Path(self.tmp.name)/'absent.db'
        with self.assertRaises((ValueError, OSError)): PortraitReader(missing,'i','s')
        self.assertFalse(missing.exists())
        reader=PortraitReader(self.path,'i','s')
        self.assertEqual(len(reader.list_plans()['plans']),1)
        with self.assertRaises(ValueError): PortraitReader(self.path,'wrong','s')
        p=plan(manual='work'); pid=self.register(p); c=self.claim(pid)
        self.s.finish(c['job_id'],1,result(c),owner='worker',now=1)
        page=reader.read_events(limit=1)
        self.assertEqual(page['events'][0]['plan_id'],pid)
        self.assertEqual(reader.read_events(cursor=page['cursor'])['events'],[])
        with self.assertRaises(ValueError): reader.read_events(max_bytes=1)
        self.s.seal_plan(pid)
        self.assertTrue(reader.read_plan_snapshot(pid)['sealed'])
        with self.assertRaises(ValueError): reader.read_events(cursor=dict(page['cursor'],database_id='bad'))


class DurabilityTests(unittest.TestCase):
    setUp=ClosedTests.setUp
    register=ClosedTests.register
    claim=ClosedTests.claim
    def test_migrate_preserves_bytes_and_excludes_legacy(self):
        from server_app.portraits.store import _LegacyPortraitStore
        from server_app.portraits.reader import PortraitReader
        from test_portrait_store_unittest import job as old_job, plan as old_plan
        other=Path(self.tmp.name)/'old.db'
        old=_LegacyPortraitStore(other,'i','s'); j=old_job()
        old.enqueue_published_plan(old_plan(j)); old.claim('old',0)
        old.finish(j['job_id'],1,{'status':'success','path':'legacy.png'})
        before=old.list_events()
        with self.assertRaises(ValueError): PortraitReader(other,'i','s')
        new=PortraitStore(other,'i','s')
        after=new.list_events()
        self.assertEqual([{k:v for k,v in e.items() if k!='plan_id'} for e in after],before)
        self.assertIsNone(new.claim('new',10,allowed_plan_ids=['a'*64]))
        self.assertEqual(PortraitReader(other,'i','s').read_events()['events'][0]['provenance'],'legacy_lineage_unknown')
        self.assertEqual(PortraitReader(other,'i','s').list_plans()['plans'],[])

    def test_membership_event_attempt_is_not_transferable(self):
        import sqlite3
        from contextlib import closing
        from server_app.portraits.reader import PortraitReader
        first=self.register(); c=self.claim(first)
        self.s.finish(c['job_id'],1,result(c),owner='worker',now=1); self.s.seal_plan(first)
        second=self.register(plan(manual='next'))
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE events SET plan_id=?',(second,))
        with self.assertRaises(ValueError): PortraitReader(self.path,'i','s').read_events()

    def test_result_profile_requires_exact_integer_and_deadline_renew(self):
        pid=self.register(); c=self.claim(pid)
        r=result(c); r['profile']['scale']=2.0
        with self.assertRaises(ValueError): self.s.finish(c['job_id'],1,r,owner='worker',now=1)
        self.assertTrue(self.s.renew(c['job_id'],1,'worker',9,2000))
        self.assertFalse(self.s.renew(c['job_id'],1,'worker',1000,60))

    def test_registration_rollback_with_member_insert_fault(self):
        import sqlite3
        from contextlib import closing
        from server_app.portraits.reader import PortraitReader
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TRIGGER reject_member BEFORE INSERT ON members BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError): self.register()
        with closing(sqlite3.connect(self.path)) as db, db: db.execute('DROP TRIGGER reject_member')
        self.assertEqual(PortraitReader(self.path,'i','s').list_plans()['plans'],[])
        self.assertIsNone(self.s.claim('legacy',0))

    def test_real_generation_and_manifest_conflicts(self):
        p=plan(True); first=self.register(p)
        for key,value in [('expected_jobset_digest','a'*64),('comparison_evidence',{'baseline':False}),('core_publication_ref',{'digest':'f'*64})]:
            changed=copy.deepcopy(p); changed[key]=value
            with self.assertRaises(ValueError): self.s.register_closed_plan(changed)
        p['tool_fingerprint']='f'*64; p['source_capture_ref']['tool_fingerprint']='f'*64
        self.assertNotEqual(first,self.register(p))

    def test_reader_byte_prefix_watermark_replacement_and_malformed(self):
        import sqlite3
        from contextlib import closing
        from server_app.portraits.reader import PortraitReader
        pid=self.register(); c=self.claim(pid)
        self.s.finish(c['job_id'],1,result(c),owner='worker',now=1); self.s.seal_plan(pid)
        second=self.register(plan(manual='next')); c=self.claim(second,2)
        self.s.finish(c['job_id'],2,result(c),owner='worker',now=3)
        reader=PortraitReader(self.path,'i','s'); one=reader.read_events(limit=1)
        self.assertEqual(len(reader.read_events(max_bytes=one['bytes'])['events']),1)
        self.assertEqual(len(reader.read_events(watermark=1)['events']),1)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE events SET result='{}' WHERE id=2")
        with self.assertRaises(ValueError): reader.read_events(after_id=1)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE metadata SET database_id=?',('0'*64,))
        with self.assertRaises(ValueError): reader.list_plans()

    def test_finish_defer_race_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        pid=self.register(); c=self.claim(pid); barrier=threading.Barrier(2)
        def go(finish):
            store=PortraitStore(self.path,'i','s'); barrier.wait(timeout=10)
            if finish: return store.finish(c['job_id'],1,result(c),owner='worker',now=1)
            return store.defer(c['job_id'],1,'worker',1,'updater')
        with ThreadPoolExecutor(max_workers=2) as pool: self.assertEqual(sum(pool.map(go,[True,False])),1)
        self.assertLessEqual(len(self.s.list_events()),1)


class ReaderValidationTests(unittest.TestCase):
    setUp=ClosedTests.setUp
    register=ClosedTests.register
    claim=ClosedTests.claim

    def test_legacy_malformed_result_is_not_silently_accepted(self):
        from test_portrait_store_unittest import job as old_job, plan as old_plan
        from server_app.portraits.reader import PortraitReader
        from contextlib import closing
        import sqlite3
        j=old_job(); self.s.enqueue_published_plan(old_plan(j)); self.s.claim('old',0)
        self.s.finish(j['job_id'],1,{'status':'success'})
        with closing(sqlite3.connect(self.path)) as db, db: db.execute("UPDATE events SET result='{}'")
        with self.assertRaises(ValueError): PortraitReader(self.path,'i','s').read_events()

    def test_summary_has_scoped_key_and_generation_context(self):
        p=plan(True); pid=self.register(p); final=self.s.seal_plan(pid)
        self.assertEqual(final.get('summary_key'),digest(['portrait_summary','i','s',pid]))
        for key in ('kind','version','input_snapshot_digest','tool_fingerprint'):
            self.assertEqual(final.get(key),p[key])

class SnapshotRaceTests(unittest.TestCase):
    setUp=ClosedTests.setUp
    register=ClosedTests.register
    claim=ClosedTests.claim

    def test_live_wal_reader_and_seal_finish_race(self):
        import sqlite3
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from server_app.portraits.reader import PortraitReader
        keeper=sqlite3.connect(self.path,isolation_level=None)
        self.addCleanup(keeper.close)
        keeper.execute('PRAGMA wal_autocheckpoint=0')
        reader=PortraitReader(self.path,'i','s')
        before=(keeper.execute('PRAGMA user_version').fetchone(),keeper.execute('PRAGMA journal_mode').fetchone())
        pid=self.register(); c=self.claim(pid); barrier=threading.Barrier(2)
        def go(finish):
            store=PortraitStore(self.path,'i','s'); barrier.wait(timeout=10)
            return store.finish(c['job_id'],1,result(c),owner='worker',now=1) if finish else store.seal_plan(pid)
        with ThreadPoolExecutor(max_workers=2) as pool: outcomes=list(pool.map(go,[True,False]))
        self.assertTrue(outcomes[0])
        self.assertTrue(Path(str(self.path)+'-wal').exists())
        self.assertEqual(len(reader.read_events()['events']),1)
        final=self.s.seal_plan(pid)
        if outcomes[1] is not None: self.assertEqual(outcomes[1],final)
        self.assertEqual(reader.read_plan_snapshot(pid),final)
        self.assertEqual(before,(keeper.execute('PRAGMA user_version').fetchone(),keeper.execute('PRAGMA journal_mode').fetchone()))

    def test_expiry_backoff_is_persisted_and_counted_once(self):
        p=plan(); p['recovery']['backoff_seconds']=5
        pid=self.register(p); self.claim(pid)
        self.assertIsNone(self.claim(pid,10))
        self.s=PortraitStore(self.path,'i','s')
        self.assertIsNone(self.claim(pid,14))
        c=self.claim(pid,15)
        self.assertEqual((c['attempt'],c['crash_count']),(2,1))



class ReviewRegressionTests(unittest.TestCase):
    setUp=ClosedTests.setUp
    register=ClosedTests.register
    claim=ClosedTests.claim

    def finish_plan(self,p,t):
        pid=self.register(p); c=self.claim(pid,t)
        self.s.finish(c['job_id'],c['attempt'],result(c),owner='worker',now=t+1)
        self.s.seal_plan(pid)
        return pid

    def test_backup_replacement_rejects_live_and_persisted_cursors(self):
        import sqlite3, os
        from contextlib import closing
        from server_app.portraits.reader import PortraitReader
        self.finish_plan(plan(),0)
        saved=self.path.with_name('backup.db')
        with closing(sqlite3.connect(self.path)) as a, closing(sqlite3.connect(saved)) as b: a.backup(b)
        self.finish_plan(plan(manual='second'),2)
        reader=PortraitReader(self.path,'i','s'); cursor=reader.read_events()['cursor']; plans_cursor=reader.list_plans()['cursor']
        os.replace(saved,self.path)
        for current in (reader,PortraitReader(self.path,'i','s')):
            with self.assertRaises(ValueError): current.read_events(cursor=cursor)
            with self.assertRaises(ValueError): current.list_plans(cursor=plans_cursor)
        self.finish_plan(plan(manual='third'),4)
        fresh=PortraitReader(self.path,'i','s')
        self.assertEqual([e['id'] for e in fresh.read_events()['events']],[1,2])
        with self.assertRaises(ValueError): fresh.read_events(cursor=cursor)

    def test_same_file_truncation_and_reused_event_id_reject_cursor(self):
        import sqlite3
        from contextlib import closing
        from server_app.portraits.reader import PortraitReader
        self.finish_plan(plan(),0); self.finish_plan(plan(manual='second'),2)
        reader=PortraitReader(self.path,'i','s'); cursor=reader.read_events()['cursor']
        with closing(sqlite3.connect(self.path)) as db, db: db.execute('DELETE FROM events WHERE id=2')
        with self.assertRaises(ValueError): reader.read_events(cursor=cursor)
        with self.assertRaises(ValueError): reader.read_events(after_id=2)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO events SELECT 2,event_key||'x',job_id,attempt+10,instance_id,source_id,version,result,plan_id FROM events WHERE id=1")
        with self.assertRaises(ValueError): reader.read_events(cursor=cursor)

    def test_native_prepared_ref_distinct_content_and_snapshot_hashes(self):
        from dataclasses import asdict
        from server_app.portraits.source_prepare import PreparedSourceRef
        from server_app.portraits.source_io import MetadataRef
        p=plan(True)
        metadata=MetadataRef(digest(['i','s']),'a'*64,'capture.json',123,'d'*64)
        ref=PreparedSourceRef('portrait-prepared-v1','i','s','v','a'*64,'c'*64,metadata,'b'*64,0,0,0,0)
        p['source_capture_ref']=asdict(ref)
        self.assertNotEqual(ref.capture.sha256,ref.snapshot_digest)
        self.register(p)

    def test_registered_null_plan_cannot_downgrade_even_after_transfer(self):
        import sqlite3
        from contextlib import closing
        from server_app.portraits.reader import PortraitReader
        self.finish_plan(plan(),0); self.register(plan(manual='transfer'))
        with closing(sqlite3.connect(self.path)) as db, db: db.execute('UPDATE events SET plan_id=NULL')
        with self.assertRaises(ValueError): PortraitReader(self.path,'i','s').read_events()
        with closing(sqlite3.connect(self.path)) as db, db: db.execute("UPDATE events SET result=?", ('{"status":"success"}',))
        with self.assertRaises(ValueError): PortraitReader(self.path,'i','s').read_events()


class ReviewContinuityTests(unittest.TestCase):
    setUp=ClosedTests.setUp
    register=ClosedTests.register
    claim=ClosedTests.claim
    finish_plan=ReviewRegressionTests.finish_plan

    def test_cursor_survives_commits_wal_and_plan_sealing_but_not_other_stream(self):
        import sqlite3
        from server_app.portraits.reader import PortraitReader
        keeper=sqlite3.connect(self.path); self.addCleanup(keeper.close)
        self.finish_plan(plan(),0)
        reader=PortraitReader(self.path,'i','s'); events=reader.read_events()['cursor']
        pid=self.register(plan(manual='next')); plans=reader.list_plans()['cursor']
        c=self.claim(pid,2); self.s.finish(c['job_id'],c['attempt'],result(c),owner='worker',now=3)
        self.s.seal_plan(pid)
        restarted=PortraitReader(self.path,'i','s')
        self.assertEqual([e['id'] for e in restarted.read_events(cursor=events)['events']],[2])
        self.assertEqual(restarted.list_plans(cursor=plans)['plans'],[])
        with self.assertRaises(ValueError): restarted.read_events(cursor=plans)
        with self.assertRaises(ValueError): restarted.list_plans(cursor=events)

    def test_core_publication_has_distinct_valid_scope_namespace(self):
        p=plan(True); p['core_publication_ref']['scope_key']='f'*64
        pid=self.register(p)
        from server_app.portraits.reader import PortraitReader
        self.assertEqual(PortraitReader(self.path,'i','s').read_manifest(pid)['core_publication_ref'],p['core_publication_ref'])

    def test_core_publication_pin_must_match_prepared_source(self):
        p=plan(True)
        self.assertNotEqual(p['source_capture_ref']['capture']['sha256'],p['input_snapshot_digest'])
        p['core_publication_ref']['pin_digest']='f'*64
        with self.assertRaisesRegex(ValueError,'core publication pin mismatch'):
            self.register(p)

    def test_prepared_ref_checks_independent_identity_snapshot_and_tool(self):
        for key,value in [('instance_id','other'),('snapshot_digest','a'*64),('tool_fingerprint','a'*64),('pin_digest','b'*64)]:
            p=plan(True); p['source_capture_ref'][key]=value
            with self.assertRaises(ValueError): self.register(p)
        p=plan(True); p['source_capture_ref']['digest']=p['input_snapshot_digest']
        with self.assertRaises(ValueError): self.register(p)

if __name__=='__main__': unittest.main()
