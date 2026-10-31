"""Behavioral checks for the durable local queue; no renderer or server."""
import copy
from contextlib import closing
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from server_app.portraits.store import PortraitStore
from server_app.portraits.model import digest, PROFILE


def job(skin='2', version='v'):
    j = dict(schema_version=1, instance_id='i', source_id='s', version=version,
             character_id='1', skin_id=skin, render_key='a'*64,
             profile=copy.deepcopy(PROFILE), reason='new_skin')
    j['job_id'] = digest([j[k] for k in ('instance_id','source_id','version','character_id','skin_id','render_key')])
    return j


def plan(*jobs):
    return dict(schema_version=1, instance_id='i', source_id='s', version='v', published=True, jobs=list(jobs))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'q.db'
        self.store = PortraitStore(self.path, 'i', 's')

    def test_recovery_fencing_events_and_scope(self):
        a, b = self.store, PortraitStore(self.path,'i','s')
        j = job(); j['inputs'] = {'package':'frozen/path','hashes':['b'*64]}
        self.assertEqual(a.enqueue_published_plan(plan(j)),1)
        self.assertEqual(b.enqueue_published_plan(plan(j)),0)
        x = a.claim('a',0,10)
        self.assertEqual((x['attempt'],x['owner'],x['lease_until']),(1,'a',10))
        self.assertIsNone(b.claim('b',1))
        y = b.claim('b',10,10)
        self.assertEqual(y['attempt'],2)
        self.assertEqual(y['version'],'v')
        self.assertEqual(y['inputs'],j['inputs'])
        self.assertFalse(a.finish(j['job_id'],1,{'status':'success'}))
        self.assertTrue(b.finish(j['job_id'],2,{'status':'success','path':'out.png'}))
        self.assertFalse(b.finish(j['job_id'],2,{'status':'success'}))
        events = PortraitStore(self.path,'i','s').list_events()
        self.assertEqual(len(events),1)
        self.assertEqual({k:events[0][k] for k in ('job_id','attempt','instance_id','source_id','version','result')},
                         dict(job_id=j['job_id'],attempt=2,instance_id='i',source_id='s',version='v',result={'status':'success','path':'out.png'}))
        self.assertFalse(a.requeue(j['job_id']))
        with self.assertRaises(ValueError): PortraitStore(self.path,'other','s')

    def test_plan_validation_is_atomic(self):
        for key,value in [('published',False),('published',1),('schema_version',True),('version',''),('version',1),('instance_id','other'),('source_id','other'),('jobs',{}),('jobs',None)]:
            with self.subTest(key=key,value=value):
                p = plan(job()); p[key] = value
                with self.assertRaises(ValueError): self.store.enqueue_published_plan(p)
        for key,value in [('schema_version',2),('schema_version',True),('version','other'),('instance_id','other'),('source_id','other'),('character_id',1),('skin_id','02'),('skin_id','0'),('render_key','A'*64),('job_id','b'*64),('reason','other'),('profile',dict(PROFILE,scale=2.0)),('profile',dict(PROFILE,extra=1))]:
            with self.subTest(key=key,value=value):
                bad = job('3'); bad[key] = value
                with self.assertRaises(ValueError): self.store.enqueue_published_plan(plan(job(),bad))
                self.assertIsNone(self.store.claim('w',0))

    def test_duplicate_and_conflicting_payload_roll_back(self):
        j = job()
        with self.assertRaises(ValueError): self.store.enqueue_published_plan(plan(j,j))
        self.assertIsNone(self.store.claim('w',0))
        self.store.enqueue_published_plan(plan(j))
        conflict = copy.deepcopy(j); conflict['inputs'] = {'changed':True}
        with self.assertRaises(ValueError): self.store.enqueue_published_plan(plan(job('3'),conflict))
        self.assertEqual(self.store.claim('w',0)['job_id'],j['job_id'])
        self.assertIsNone(self.store.claim('w2',1))

    def concurrent_claims(self):
        barrier = threading.Barrier(2)
        def claim(owner):
            queue = PortraitStore(self.path,'i','s')
            barrier.wait(timeout=10)
            return queue.claim(owner,0,10)
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(claim,['a','b']))

    def test_concurrent_single_job_has_one_owner(self):
        self.store.enqueue_published_plan(plan(job()))
        self.assertEqual(sum(x is not None for x in self.concurrent_claims()),1)

    def test_concurrent_claims_and_oldest_order(self):
        self.store.enqueue_published_plan(plan(job('2'),job('3')))
        a,b = self.concurrent_claims()
        self.assertNotEqual(a['job_id'],b['job_id'])
        self.assertEqual([a['attempt'],b['attempt']],[1,1])
        self.assertIsNone(self.store.claim('c',1))
        self.assertEqual(self.store.claim('c',10)['job_id'],job('2')['job_id'])

    def test_numeric_and_owner_validation(self):
        for now in [float('nan'),float('inf'),-float('inf'),True,'0',None]:
            with self.subTest(now=now),self.assertRaises(ValueError): self.store.claim('w',now)
        for lease in [0,-1,float('nan'),float('inf'),True,'60',86401]:
            with self.subTest(lease=lease),self.assertRaises(ValueError): self.store.claim('w',0,lease)
        for owner in ['', ' ',1,None]:
            with self.assertRaises(ValueError): self.store.claim(owner,0)
        with self.assertRaises(ValueError): self.store.claim('w',1e308,60)
        for cursor in [-1,True,1.2,'0']:
            with self.assertRaises(ValueError): self.store.list_events(cursor)

    def test_finish_validation_preserves_claim(self):
        j = job(); self.store.enqueue_published_plan(plan(j)); self.store.claim('w',0)
        bad = [None,{}, {'status':''},{'status':'running'},{'status':'success','x':float('nan')},
               {'status':'success','x':object()},{'status':'success','x':(1,2)},
               {'status':'success',1:'bad key'},{'status':'success','x':'a'*(1024*1024)}]
        circular = {'status':'success'}; circular['x'] = circular; bad.append(circular)
        nested = {}
        for _ in range(70): nested = {'x':nested}
        bad.append(dict(status='success',nested=nested))
        for result in bad:
            with self.assertRaises(ValueError): self.store.finish(j['job_id'],1,result)
        for attempt in [True,0,1.0,'1']:
            with self.assertRaises(ValueError): self.store.finish(j['job_id'],attempt,{'status':'success'})
        self.assertEqual(self.store.list_events(),[])
        self.assertTrue(self.store.finish(j['job_id'],1,{'status':'success'}))

    def test_job_json_is_bounded_and_strict(self):
        for extra in [object(),float('inf'),(1,2),'x'*(1024*1024)]:
            j = job(); j['extra'] = extra
            with self.assertRaises(ValueError): self.store.enqueue_published_plan(plan(j))
        self.assertIsNone(self.store.claim('w',0))

    def test_event_failure_rolls_back_terminal_state(self):
        j = job(); self.store.enqueue_published_plan(plan(j)); self.store.claim('w',0)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TRIGGER reject_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError): self.store.finish(j['job_id'],1,{'status':'success'})
        self.assertEqual(self.store.list_events(),[])
        with closing(sqlite3.connect(self.path)) as db, db: db.execute('DROP TRIGGER reject_event')
        self.assertTrue(self.store.finish(j['job_id'],1,{'status':'success'}))

    def test_requeue_retains_audit_and_monotonic_attempt(self):
        for skin,status in [('2','failed'),('3','blocked'),('4','cancelled')]:
            j = job(skin); self.store.enqueue_published_plan(plan(j))
            self.assertFalse(self.store.requeue(j['job_id']))
            self.store.claim('w',0)
            self.assertFalse(self.store.requeue(j['job_id']))
            self.assertTrue(self.store.finish(j['job_id'],1,{'status':status}))
            self.assertTrue(self.store.requeue(j['job_id']))
            self.assertFalse(self.store.requeue(j['job_id']))
            self.assertEqual(self.store.claim('w',0)['attempt'],2)
            self.assertTrue(self.store.finish(j['job_id'],2,{'status':'success'}))
        events = self.store.list_events()
        self.assertEqual(len(events),6)
        self.assertEqual(len({e['event_key'] for e in events}),6)
        self.assertEqual(self.store.list_events(events[2]['id']),events[3:])
        self.assertEqual(PortraitStore(self.path,'i','s').list_events(),events)
        self.assertFalse(self.store.requeue('f'*64))

    def test_schema_and_scope_fail_closed(self):
        for instance,source in [('', 's'),('i',' '),(1,'s')]:
            with self.assertRaises(ValueError): PortraitStore(self.path,instance,source)
        with closing(sqlite3.connect(self.path)) as db, db: db.execute('PRAGMA user_version=999')
        with self.assertRaises(ValueError): PortraitStore(self.path,'i','s')
        other = Path(self.tmp.name)/'unknown.db'
        with closing(sqlite3.connect(other)) as db, db: db.execute('CREATE TABLE unrelated (id INTEGER)')
        with self.assertRaises(ValueError): PortraitStore(other,'i','s')

    def test_matching_columns_without_constraints_are_incompatible(self):
        other = Path(self.tmp.name)/'spoof.db'
        with closing(sqlite3.connect(other)) as db, db:
            db.execute('PRAGMA user_version=1')
            db.execute('CREATE TABLE metadata(singleton, schema_version, instance_id, source_id)')
            db.execute("INSERT INTO metadata VALUES (1,1,'i','s')")
            db.execute('CREATE TABLE jobs(seq,job_id,payload,version,state,attempt,owner,lease_until,result)')
            db.execute('CREATE TABLE events(id,event_key,job_id,attempt,instance_id,source_id,version,result)')
        with self.assertRaises(ValueError): PortraitStore(other,'i','s')


if __name__ == '__main__': unittest.main()
