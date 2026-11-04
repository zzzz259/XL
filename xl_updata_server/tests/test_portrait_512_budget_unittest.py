"""Synthetic budget contracts; no Native or live service execution."""
import copy
import dataclasses
import json
import unittest
from pathlib import Path

import test_portrait_render_unittest as render_tests
import test_portrait_source_transfer_unittest as transfer_tests
from server_app.portraits import render_protocol, source_download_worker, source_worker
from server_app.portraits.executor import ResourcePolicy
from server_app.portraits.metadata_runtime import MetadataConfig, MetadataRuntime
from test_portrait_source_worker_unittest import request_for


class BudgetTests(unittest.TestCase):
    def test_policy_default_and_bounds(self):
        root = Path.cwd()
        args = (root / 'lease', (root / 'guard',), (root,))
        policy = ResourcePolicy(*args)
        self.assertEqual((policy.memory_bytes, policy.admission_bytes, policy.abort_bytes),
                         (536870912, 671088640, 134217728))
        for cap in (1, 268435456, 536870912):
            self.assertEqual(ResourcePolicy(*args, memory_bytes=cap).memory_bytes, cap)
        for cap in (True, False, 0, -1, 1.5, '512', float('inf'), float('nan'), 536870913):
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                ResourcePolicy(*args, memory_bytes=cap)

    def test_accepts_512_mib(self):
        root = Path.cwd()
        self.assertEqual(ResourcePolicy(root / 'lease', (root / 'guard',), (root,),
                                        memory_bytes=536870912).memory_bytes, 536870912)

    def test_public_workers_reject_old_reserves(self):
        root = Path.cwd()
        envelopes = ((source_worker.validate_request, request_for(root)),
                     (source_download_worker.validate_request, transfer_tests.ProtocolTests().request()),
                     (render_protocol.validate_request, render_tests.EnvelopeTests().request(root)))
        for validate, envelope in envelopes:
            validate(envelope)
            for field, floor in (('admission_bytes', 671088640), ('abort_bytes', 134217728)):
                for value in (floor - 1, floor // 2):
                    changed = copy.deepcopy(envelope)
                    changed['policy'][field] = value
                    with (self.subTest(worker=validate.__module__, field=field, value=value),
                          self.assertRaises(ValueError)):
                        validate(changed)

    def test_metadata_rejects_old_reserves_before_runtime(self):
        root = Path.cwd()
        policy = ResourcePolicy(root / 'lease', (root / 'guard',), (root,))
        config = MetadataConfig(enabled=True, instance_id='i', source_id='s',
            cdn_base='https://fixture.invalid/Android/Bundles',
            updater_data_root=root / 'updater', prepared_source_root=root / 'source',
            portrait_root=root / 'portrait', work_root=root / 'work',
            ledger_path=root / 'ledger/l.db', store_path=root / 'store/s.db')
        for field, floor in (('admission_bytes', 671088640), ('abort_bytes', 134217728)):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'metadata .*floor'):
                MetadataRuntime(dataclasses.replace(config, resource_policy=
                    dataclasses.replace(policy, **{field: floor - 1})))

    def test_disabled_companions_and_examples(self):
        root = Path(__file__).resolve().parents[2] / 'xl_deploy'
        for tier in ('main', 'test'):
            text = (root / 'deploy' / f'xl-portrait-{tier}.service').read_text()
            for line in ('MemoryHigh=384M', 'MemoryMax=512M', 'MemorySwapMax=0',
                         'CPUQuota=100%', 'CPUAffinity=1', 'LimitCORE=0'):
                self.assertIn(line, text)
            self.assertNotIn('[Install]', text)
            config = json.loads((root / 'examples/portrait' / f'{tier}.runtime.json.example').read_text())
            policy = config['metadata']['resource_policy']
            self.assertEqual((policy['memory_bytes'], policy['admission_bytes'], policy['abort_bytes']),
                             (536870912, 671088640, 134217728))
