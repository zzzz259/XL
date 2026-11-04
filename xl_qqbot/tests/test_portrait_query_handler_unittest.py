import unittest
from bot_app.selection import SelectionStore

class PortraitSelectionTests(unittest.TestCase):
    def test_intent_survives_selection_and_isolation(self):
        store = SelectionStore()
        item = object()
        store.set('g', 'u', '角色', [item], intent='portrait', portrait_ordinal=3, catalog_version='v')
        pending = store.get('g', 'u')
        self.assertEqual((pending.intent, pending.portrait_ordinal, pending.catalog_version), ('portrait', 3, 'v'))
        self.assertIsNone(store.get('g', 'other'))
        self.assertIs(store.choose('g', 'u', '1')[1], item)

class PortraitConfigTests(unittest.TestCase):
    def test_defaults(self):
        from bot_app.config import PortraitQueryConfig
        cfg = PortraitQueryConfig()
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.max_decode_pixels, 8 * 1024 * 1024)

class PortraitHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_portrait_and_card_routes(self):
        from bot_app.query_handler import QueryHandler
        from types import SimpleNamespace
        class Matcher:
            clean = staticmethod(str.strip)
            exact_name_exists = staticmethod(lambda q: q == 'Unit2')
            def match(self, q):
                return SimpleNamespace(kind='direct', match=SimpleNamespace(character_id='1', name=q, score=100, via='exact'))
        class Reader:
            def __init__(self): self.calls=[]
            def find(self, *args):
                self.calls.append(args)
                return dict(status='found', image_path='/safe/test.png')
        class Sender:
            def __init__(self): self.portraits=[]; self.cards=[]; self.texts=[]
            async def send_schedule_image(self, *args): self.portraits.append(args); return {'g': True}
            async def send_image(self, *args): self.cards.append(args); return True
            async def send_text(self, *args): self.texts.append(args)
        reader=Reader(); sender=Sender()
        card=SimpleNamespace(find=lambda cid: SimpleNamespace(status='found', image_path='/safe/card.png'))
        handler=QueryHandler(card, Matcher(), SelectionStore(), sender, portrait_querier=reader)
        await handler._answer_query('g', 'Unit 3', 'u')
        self.assertEqual(reader.calls, [('1', 3)])
        self.assertEqual(len(sender.portraits), 1)
        await handler._answer_query('g', 'Unit2', 'u')
        self.assertEqual(len(sender.cards), 1)
        await handler._answer_query('g', 'Unit3', 'u')
        self.assertEqual(reader.calls[-1], ('1', 3))
        await handler._answer_query('g', 'Unit ' + '9' * 100, 'u')
        self.assertTrue(sender.texts)
        handler._mute.muted = True
        before = len(reader.calls)
        await handler._answer_query('g', 'Unit 3', 'u')
        self.assertEqual(len(reader.calls), before)

class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import tempfile, json
        from pathlib import Path
        from types import SimpleNamespace
        from bot_app.matcher import CharacterMatcher
        from bot_app.query_handler import QueryHandler
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        data = root / 'characters.json'
        data.write_text(json.dumps({'characters': {'1': {'name':'雾铃','star':5}, '2': {'name':'雾灵','star':5}, '3': {'name':'Unit2','star':5}}}), encoding='utf-8')
        self.matcher = CharacterMatcher(data, root)
        class Sender:
            def __init__(self): self.texts=[]; self.images=[]; self.cards=[]; self.ok=True
            async def send_text(self,*args): self.texts.append(args[1])
            async def send_schedule_image(self,*args): self.images.append(args); return self.ok
            async def send_image(self,*args): self.cards.append(args); return True
        class Reader:
            def __init__(self): self.calls=[]; self.result={'status':'not_generated'}
            def find(self,*args): self.calls.append(args); return self.result
        self.sender=Sender(); self.reader=Reader(); self.store=SelectionStore()
        self.handler=QueryHandler(SimpleNamespace(find=lambda cid: SimpleNamespace(status='found',image_path='card.png')),self.matcher,self.store,self.sender,portrait_querier=self.reader)

    async def test_candidates_keep_ordinal_isolated_and_overwritten(self):
        await self.handler._answer_query('g','wuling 3','u')
        pending=self.store.get('g','u')
        self.assertIsNotNone(pending)
        self.assertEqual(pending.portrait_ordinal,3)
        self.assertFalse(await self.handler._handle_selection('g','other','1'))
        await self.handler._handle_selection('g','u','1')
        self.assertEqual(self.reader.calls[-1][1],3)
        self.assertFalse(self.sender.cards)
        await self.handler._answer_query('g','wuling 2','u')
        await self.handler._answer_query('g','Unit2','u')
        self.assertIsNone(self.store.get('g','u'))
        self.assertEqual(len(self.sender.cards),1)

    async def test_same_version_mismatch_caption(self):
        self.reader.result=dict(status='stale',image_path='test.png',source_version='v',catalog_version='v',reason='artifact_inputs_changed')
        await self.handler._answer_query('g','Unit2 2','u')
        self.assertNotIn('旧版本',self.sender.images[-1][1])
        self.assertIn('输入或工具',self.sender.images[-1][1])

    async def test_same_version_retained_artifact_caption_is_neutral(self):
        self.reader.result = {
            'status': 'stale', 'image_path': 'test.png', 'source_version': 'v',
            'catalog_version': 'v', 'reason': 'retained_artifact',
        }
        original = self.reader.result.copy()
        await self.handler._answer_query('g', 'Unit2 2', 'u')
        self.assertEqual(
            self.sender.images[-1][1], '展示已生成的立绘，来源版本 v',
        )
        self.assertEqual(self.reader.result, original)
        self.assertFalse(self.sender.texts)

    async def test_states_and_safe_upload_failures(self):
        self.reader.result={'status':'out_of_range','available_skins':[{'ordinal':1,'name':'原版','state':'complete'},{'ordinal':2,'name':'占位','state':'failed'}]}
        await self.handler._answer_query('g','Unit2 9','u')
        self.assertIn('1. 原版 [complete]',self.sender.texts[-1])
        self.assertIn('2. 占位 [failed]',self.sender.texts[-1])
        self.reader.result={'status':'not_generated'}
        await self.handler._answer_query('g','Unit2 2','u')
        self.assertIn('未生成',self.sender.texts[-1])
        self.reader.result={'status':'stale','image_path':'test.png','source_version':'old','catalog_version':'new'}
        await self.handler._answer_query('g','Unit2 2','u')
        self.assertIn('old',self.sender.images[-1][1]); self.assertIn('new',self.sender.images[-1][1])
        self.sender.ok=False
        await self.handler._answer_query('g','Unit2 2','u')
        self.assertIn('上传失败',self.sender.texts[-1])
        self.sender.send_schedule_image=None
        await self.handler._answer_query('g','Unit2 2','u')
        self.assertIn('接口暂不可用',self.sender.texts[-1])

class ConfigIntegrationTests(unittest.TestCase):
    def test_invalid_config(self):
        import tempfile
        from pathlib import Path
        from bot_app.config import load_config
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'config.toml'
            for fields in ('enabled="yes"','enabled=true','enabled=true\ndata_dir="media"','max_decode_pixels=0','max_decode_pixels=true','max_image_bytes=0','max_catalog_bytes=false','max_manifest_bytes=-1'):
                with self.subTest(fields=fields):
                    path.write_text('[watch]\noutbox_dir="outbox"\n[portrait_query]\n'+fields,encoding='utf-8')
                    with self.assertRaises(ValueError): load_config(path)

    def test_service_real_config_injects_reader_without_starting_sender(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from bot_app.config import load_config
        from bot_app.service import build_app, QUERY_HANDLER_KEY
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'config.toml'
            path.write_text('[instance]\nid="demo"\n[watch]\noutbox_dir="outbox"\n[portrait_query]\nenabled=true\ndata_dir="media"\nsource_id="source"',encoding='utf-8')
            cfg=load_config(path)
            with patch('bot_app.service.QQSender') as factory:
                app=build_app(cfg)
                factory.return_value.start.assert_not_called()
                reader=app[QUERY_HANDLER_KEY]._portrait_querier
                self.assertEqual((reader.instance_id,reader.source_id),('demo','source'))

class InstanceIdentityTests(unittest.TestCase):
    def test_effective_identity_and_explicit_source(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from bot_app.config import load_config
        from bot_app.service import build_app, QUERY_HANDLER_KEY, INSTANCE_ID_KEY, resolve_instance_id
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'config.toml'
            base='[watch]\noutbox_dir="outbox"\n[portrait_query]\nenabled=true\ndata_dir="media"\nsource_id="source"\n'
            path.write_text(base,encoding='utf-8'); cfg=load_config(path)
            with self.assertRaises(ValueError):
                resolve_instance_id(SimpleNamespace(instance_id=None, tier=None), cfg)
            with patch('bot_app.service.QQSender'):
                with self.assertRaises(ValueError): build_app(cfg)
                app=build_app(cfg,'cli')
                self.assertEqual(app[QUERY_HANDLER_KEY]._portrait_querier.instance_id,'cli')
                args=SimpleNamespace(instance_id=None,tier='test')
                app=build_app(cfg,resolve_instance_id(args,cfg))
                self.assertEqual(app[QUERY_HANDLER_KEY]._portrait_querier.instance_id,app[INSTANCE_ID_KEY])
                path.write_text('[instance]\nid="default"\n'+base,encoding='utf-8')
                cfg=load_config(path)
                self.assertEqual(resolve_instance_id(args,cfg),'default')
                self.assertEqual(build_app(cfg)[QUERY_HANDLER_KEY]._portrait_querier.instance_id,'default')

if __name__ == '__main__':
    unittest.main()
