import copy
import unittest
from server_app.portraits import build_catalog, validate_catalog, plan_changes

def skin(s="10", c="1", **kw):
    return dict(character_id=str(c), skin_id=str(s), name='name', family='cardspine', config_fingerprint='a'*64, dependency_fingerprint='b'*64, tool_fingerprint='c'*64, **kw)
def cat(rows=None, version='1', instance='i'):
    return build_catalog(instance, 'source', version, rows if rows is not None else [skin()])

class PlanningTests(unittest.TestCase):
    def test_baseline_and_no_backfill(self):
        a=cat(); self.assertEqual(plan_changes(None,a),[])
        self.assertEqual(plan_changes(a,cat(version='2')),[])
    def test_sort_filter_copy(self):
        rows=[skin(20),skin(10),skin(30,family_unused=True)]; rows[-1]['family']='battlespine'
        old=copy.deepcopy(rows); a=cat(rows)
        self.assertEqual(rows,old); self.assertEqual([(s['skin_id'],s['ordinal']) for s in a['skins']],[("10",1),("20",2)])
    def test_reasons_and_determinism(self):
        a=cat(); b=cat([skin(),skin(20),skin(30,2)])
        jobs=plan_changes(a,b)
        self.assertEqual([j['reason'] for j in jobs],['new_skin','new_character'])
        self.assertEqual(jobs,plan_changes(a,b))
    def test_content_changes(self):
        for field in ('config_fingerprint','dependency_fingerprint','tool_fingerprint'):
            row=skin(); row[field]='d'*64
            self.assertEqual(plan_changes(cat(),cat([row]))[0]['reason'],'changed_inputs')
    def test_display_only(self):
        row=skin(); row['name']='renamed'
        a=cat(); b=cat([skin(5),row],version='2')
        self.assertEqual(a['skins'][0]['render_key'],b['skins'][1]['render_key'])
        self.assertEqual(len(plan_changes(a,b)),1)
    def test_isolation(self):
        self.assertNotEqual(cat()['skins'][0]['render_key'],cat(instance='other')['skins'][0]['render_key'])
        with self.assertRaises(ValueError): plan_changes(cat(),cat(instance='other'))
    def test_bad_data(self):
        for rows in ([skin(),skin()], [skin(-1)], [skin(True)]):
            with self.assertRaises(ValueError): cat(rows)
        a=cat(); a['skins'][0]['dependency_fingerprint']='path'
        with self.assertRaises(ValueError): validate_catalog(a)
        a=cat(); a['skins'][0]['render_key']='0'*64
        with self.assertRaises(ValueError): plan_changes(a,cat())
    def test_canonical_ids_and_scale(self):
        for key in ('character_id','skin_id'):
            for value in (1, True, '01', '0', '-1', '1.0'):
                row=skin(); row[key]=value
                with self.assertRaises(ValueError): cat([row])
        for value in (2.0, True):
            a=cat(); a['skins'][0]['profile']['scale']=value
            with self.assertRaises(ValueError): validate_catalog(a)
        self.assertEqual(plan_changes(cat([]),cat())[0]['skin_id'],'10')

    def test_source_mismatch(self):
        row=skin(); row['source_id']='other'
        with self.assertRaises(ValueError): cat([row])

if __name__=='__main__': unittest.main()
