import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from xl_deploy import config
from xl_deploy.planner import plan_deployment
from xl_deploy.runner import CommandRunner
from xl_deploy.state import DeploymentState
from xl_deploy.transaction import DeploymentPaths, DeploymentTransaction

SHA = '2' * 40


def token_for(instance_id):
    return f'test-token-for-{instance_id}-01234567890123456789'


CONTROL_TOKEN = 'test-control-token-012345678901234567890123'


def mapping():
    return {'instances': [
        {'instance_id': name, 'watch_ref': 'test', 'config_path': f'{name}/config.toml',
         'data_roots': [f'{name}/data'], 'units': {'bot': f'xl-{name}.service'},
         'listen_ports': {'bot': port}, 'groups': [group]}
        for name, port, group in [('test-a', 8811, 'g-a'), ('test-b', 8812, 'g-b')]]
        , 'gateway': {'unit': 'xl-gateway.service', 'watch_ref': 'main',
                      'config_path': 'gateway/config.toml', 'listen_port': 8810, 'data_roots': ['gateway/data']}}


class Pointer:
    def __init__(self): self.targets = {}
    def read(self, pointer): return self.targets.get(str(pointer))
    def replace(self, pointer, target): self.targets[str(pointer)] = target


class Runner:
    def __init__(self): self.events = []
    def stage(self, plan, candidate): candidate.mkdir(); return candidate
    def preflight(self, plan, candidate, config_path, data_path):
        self.events.append(('preflight', config_path, data_path))
    def stop(self, units): self.events.append(('stop', *units))
    def start(self, units): self.events.append(('start', *units))
    def health_check(self, units): self.events.append(('health', *units))


class Router:
    def __init__(self): self.events = []
    def announce(self, scope, text, **kwargs): return True
    def announce_release_note(self, scope, text, **kwargs): return True
    def pause(self, scope): self.events.append(('pause', *scope))
    def drain(self, scope, timeout): return True
    def resume(self, scope): self.events.append(('resume', *scope))


class ConfiguredInventoryTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(config, 'DeploymentInventory'), 'declarative DeploymentInventory is missing')
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def inventory(self, values=None):
        inventory = config.DeploymentInventory.from_mapping(values or mapping(), base_dir=self.root)
        for instance in inventory.instances:
            instance.config_path.parent.mkdir(parents=True, exist_ok=True)
            if not instance.config_path.exists():
                instance.config_path.write_text(
                    f'[instance]\nid = "{instance.instance_id}"\n[watch]\ndata_dir = "data"\noutbox_dir = "data/outbox"\n'
                    f'[router.instance_ports]\n"{instance.instance_id}" = {instance.listen_ports['bot']}\n'
                    f'[gateway]\ncontrol_token = "{CONTROL_TOKEN}"\n'
                    f'[gateway.instance_tokens]\n"{instance.instance_id}" = "{token_for(instance.instance_id)}"\n', encoding='utf-8')
        inventory.gateway.config_path.parent.mkdir(parents=True, exist_ok=True)
        if not inventory.gateway.config_path.exists():
            inventory.gateway.config_path.write_text(
                f'[watch]\ndata_dir = "data"\n[gateway]\ncontrol_port = {inventory.gateway.listen_port}\ncontrol_token = "{CONTROL_TOKEN}"\n[router.group_instances]\n' +
                ''.join(f'"{g}" = "{i}"\n' for g, i in inventory.group_routes.items()) +
                '[router.instance_ports]\n' + ''.join(f'"{i.instance_id}" = {i.listen_ports['bot']}\n' for i in inventory.instances) +
                '[gateway.instance_tokens]\n' + ''.join(f'"{i.instance_id}" = "{token_for(i.instance_id)}"\n' for i in inventory.instances), encoding='utf-8')
        return inventory

    def test_inventory_requires_a_distinct_gateway_token_for_each_bot_instance(self):
        inventory = self.inventory()
        config_text = inventory.gateway.config_path.read_text(encoding='utf-8')
        config_text = config_text.split('[gateway.instance_tokens]')[0] + '[gateway.instance_tokens]\n'
        inventory.gateway.config_path.write_text(config_text, encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'gateway instance tokens'):
            inventory.validate_runtime_configs()

    def test_inventory_rejects_transport_token_equal_to_shared_control_token(self):
        inventory = self.inventory()
        gateway_config = inventory.gateway.config_path
        config_text = gateway_config.read_text(encoding='utf-8')
        config_text = config_text.replace(CONTROL_TOKEN, token_for('test-a'), 1)
        gateway_config.write_text(config_text, encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'gateway instance tokens'):
            inventory.validate_runtime_configs()

    def transaction(self, inventory):
        releases = self.root / 'releases'; releases.mkdir()
        old = releases / ('1' * 40); old.mkdir()
        paths = DeploymentPaths(releases, self.root / 'current', self.root / 'config', self.root / 'data')
        state = DeploymentState(self.root / 'state', releases_root=releases,
                                current_path=paths.current_path, pointer_adapter=Pointer())
        state.set_current(old, current_path=paths.current_path / 'test')
        runner = Runner(); router = Router()
        return DeploymentTransaction(state, runner, router, paths, inventory=inventory), runner, router

    def test_two_test_instances_have_separate_roots_units_and_routes(self):
        inventory = self.inventory()
        self.assertEqual(tuple(i.instance_id for i in inventory.instances), ('test-a', 'test-b'))
        self.assertEqual(inventory.group_routes, {'g-a': 'test-a', 'g-b': 'test-b'})
        self.assertNotEqual(inventory.instances[0].config_path, inventory.instances[1].config_path)

    def test_bot_instance_can_own_a_separate_updater_config_and_nested_state(self):
        values = mapping()
        instance = values['instances'][0]
        instance['backend_config_path'] = 'test-a/updater.toml'
        instance['units']['backend'] = 'xl-test-a-updater.service'
        instance['listen_ports']['backend'] = 8831
        instance['data_roots'] = ['test-a/data']
        inventory = self.inventory(values)

        bot_config = inventory.instances[0].config_path
        bot_config.write_text(
            bot_config.read_text(encoding='utf-8').replace('data_dir = "data"', 'data_dir = "data/bot-state"'),
            encoding='utf-8')
        backend_config = inventory.instances[0].backend_config_path
        backend_config.parent.mkdir(parents=True, exist_ok=True)
        backend_config.write_text(
            '[server]\ninstance_id = "test-a"\n[paths]\ndata_dir = "data"\n'
            '[api]\nenabled = true\nhost = "127.0.0.1"\nport = 8831\n', encoding='utf-8')

        inventory.validate_runtime_configs()

    def test_backend_config_path_must_be_unique_and_outside_other_roots(self):
        values = mapping()
        values['instances'][0]['backend_config_path'] = 'test-b/config.toml'
        values['instances'][0]['units']['backend'] = 'xl-test-a-updater.service'
        values['instances'][0]['listen_ports']['backend'] = 8831
        with self.assertRaises(ValueError):
            self.inventory(values)

    def test_bot_and_backend_units_require_separate_runtime_configs(self):
        values = mapping()
        values['instances'][0]['units']['backend'] = 'xl-test-a-updater.service'
        values['instances'][0]['listen_ports']['backend'] = 8831
        with self.assertRaisesRegex(ValueError, 'separate backend_config_path'):
            self.inventory(values)

    def test_backend_healthcheck_uses_the_updater_config(self):
        from xl_deploy.runner import CommandRunner

        values = mapping()
        values['instances'][0]['backend_config_path'] = 'test-a/updater.toml'
        values['instances'][0]['units']['backend'] = 'xl-test-a-updater.service'
        values['instances'][0]['listen_ports']['backend'] = 8831
        inventory = self.inventory(values)
        instance = inventory.instances[0]
        instance.backend_config_path.parent.mkdir(parents=True, exist_ok=True)
        instance.backend_config_path.write_text(
            '[server]\ninstance_id = "test-a"\n[paths]\ndata_dir = "data"\n'
            '[api]\nenabled = true\nhost = "127.0.0.1"\nport = 8831\n', encoding='utf-8')
        inventory.validate_runtime_configs()
        commands = []
        runner = CommandRunner(inventory=inventory, executor=lambda args, **_kwargs: commands.append(args),
                               deployment_root=self.root / 'deploy')
        runner._wait_for_http_health = lambda *_args: None

        runner._inventory_health_check('xl-test-a-updater.service')

        self.assertIn(str(instance.backend_config_path), commands[0])

    def test_duplicate_resources_are_rejected(self):
        for field, value in [('listen_ports', {'bot': 8811}), ('config_path', 'test-a/config.toml'),
                             ('data_roots', ['test-a/data/child']), ('groups', ['g-a']),
                             ('units', {'bot': 'xl-test-a.service'}), ('instance_id', 'test-a')]:
            with self.subTest(field=field):
                values = mapping(); values['instances'][1][field] = value
                with self.assertRaises(ValueError): self.inventory(values)

    def test_gateway_port_and_unit_cannot_be_owned_by_instance(self):
        for field, value in [('listen_ports', {'bot': 8810}), ('units', {'bot': 'xl-gateway.service'})]:
            values = mapping(); values['instances'][1][field] = value
            with self.assertRaises(ValueError): self.inventory(values)

    def test_one_snapshot_update_selects_every_matching_instance(self):
        plan = plan_deployment('test', SHA, ['xl_qqbot/bot_app/service.py'], inventory=self.inventory())
        self.assertEqual(plan.impacted_units, ('xl-test-a.service', 'xl-test-b.service'))
        self.assertEqual(plan.impacted_tiers, ('test-a', 'test-b'))
        self.assertEqual(plan.announcement_scope, ('test-a', 'test-b'))

    def test_watch_ref_not_instance_name_selects_snapshot(self):
        values = mapping(); values['instances'][1]['watch_ref'] = 'main'
        inv = self.inventory(values)
        self.assertEqual(plan_deployment('test', SHA, ['xl_qqbot/bot_app/service.py'], inventory=inv).impacted_units,
                         ('xl-test-a.service',))
        self.assertEqual(plan_deployment('main', SHA, ['xl_qqbot/bot_app/service.py'], inventory=inv).impacted_units,
                         ('xl-test-b.service',))

    def test_gateway_update_is_single_transport_and_pauses_all_routes(self):
        plan = plan_deployment('main', SHA, ['xl_qqbot/bot_app/gateway.py'], inventory=self.inventory())
        self.assertEqual(plan.impacted_units, ('xl-gateway.service',))
        self.assertEqual(plan.announcement_scope, ('test-a', 'test-b'))
        self.assertEqual(plan.impacted_tiers, ('test-a', 'test-b'))
        self.assertEqual(plan_deployment('debug', SHA, ['xl_qqbot/bot_app/service.py'], inventory=self.inventory()).impacted_units, ())

    def test_transaction_preflights_all_configs_then_stops_all_units(self):
        inv = self.inventory(); tx, runner, router = self.transaction(inv)
        plan = plan_deployment('test', SHA, ['xl_qqbot/bot_app/service.py'], inventory=inv)
        self.assertEqual(tx.execute(plan).status, 'success')
        self.assertEqual([e[1] for e in runner.events if e[0] == 'preflight'],
                         [i.config_path for i in inv.instances])
        self.assertIn(('stop', 'xl-test-a.service', 'xl-test-b.service'), runner.events)
        self.assertIn(('pause', 'test-a', 'test-b'), router.events)

    def test_transaction_rejects_incomplete_plan_before_stopping(self):
        inv = self.inventory(); tx, runner, router = self.transaction(inv)
        plan = SimpleNamespace(branch='test', sha=SHA, changed_paths=('xl_qqbot/bot_app/service.py',),
                               impacted_units=('xl-test-a.service',), impacted_tiers=('test-a',), announcement_scope=('test-a',))
        with self.assertRaises(ValueError): tx.execute(plan)
        self.assertEqual(runner.events, []); self.assertEqual(router.events, [])

    def test_transaction_rejects_release_local_instance_data_before_stopping(self):
        values = mapping(); values['instances'][1]['data_roots'] = ['releases/runtime-data']
        inv = self.inventory(values); tx, runner, router = self.transaction(inv)
        with self.assertRaises(ValueError):
            tx.execute(plan_deployment('test', SHA, ['xl_qqbot/bot_app/service.py'], inventory=inv))
        self.assertEqual(runner.events, []); self.assertEqual(router.events, [])

    def test_runner_allowlist_and_health_use_inventory(self):
        inv = self.inventory(); commands = []; health = []
        runner = CommandRunner(inventory=inv, executor=lambda args, **kwargs: commands.append(args))
        runner._wait_for_http_health = lambda request, unit: health.append((unit, request.full_url))
        runner.stop(('xl-test-a.service', 'xl-test-b.service'))
        runner.health_check(('xl-test-a.service', 'xl-test-b.service'))
        self.assertEqual(health, [('xl-test-a.service', 'http://127.0.0.1:8811/health'),
                                  ('xl-test-b.service', 'http://127.0.0.1:8812/health')])
        with self.assertRaises(ValueError): runner.stop(('unknown.service',))

    def test_runner_prepares_each_service_environment_once(self):
        inv = self.inventory(); commands = []; release = self.root / 'candidate'
        (release / 'xl_qqbot').mkdir(parents=True)
        (release / 'xl_qqbot' / 'requirements.txt').write_text('')
        runner = CommandRunner(inventory=inv, executor=lambda args, **kwargs: commands.append(args))
        plan = plan_deployment('test', SHA, ['xl_qqbot/bot_app/service.py'], inventory=inv)
        runner.preflight(plan, release, inv.instances[0].config_path, inv.instances[0].data_roots[0])
        self.assertEqual(len([args for args in commands if args[1:3] == ['-m', 'venv']]), 2)

    def test_runtime_loads_hand_maintained_inventory(self):
        from xl_deploy.__main__ import load_runtime_config
        source = Path('xl_deploy/config.toml.example').read_text(encoding='utf-8')
        # The sample itself must offer an editable inventory.
        target = self.root / 'deploy.toml'; target.write_text(source, encoding='utf-8')
        runtime = load_runtime_config(target)
        self.assertIsNotNone(getattr(runtime, 'inventory', None))
        self.assertEqual(len(runtime.inventory.instances), 2)

    def test_read_only_inventory_dry_run_lists_all_instances_without_secrets(self):
        from xl_deploy.__main__ import render_inventory_dry_run
        inventory = self.inventory()
        output = render_inventory_dry_run(inventory)
        self.assertIn('read-only inventory check: valid', output)
        self.assertIn('gateway unit=xl-gateway.service', output)
        self.assertIn('instance_id=test-a watch_ref=test', output)
        self.assertIn('instance_id=test-b watch_ref=test', output)
        self.assertIn('groups=1', output)
        self.assertNotIn('token', output.lower())
        self.assertNotIn('g-a', output)
        self.assertNotIn('g-b', output)

    def test_dry_run_cli_skips_poller_and_secret_loading(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch

        from xl_deploy import __main__

        inventory = self.inventory()
        output = StringIO()
        with patch.object(__main__, 'load_runtime_config', return_value=SimpleNamespace(inventory=inventory)), \
                patch.object(__main__, 'build_poller', side_effect=AssertionError('poller must not run')), \
                redirect_stdout(output):
            self.assertEqual(__main__.main(['--config', 'unused.toml', '--dry-run-inventory']), 0)
        self.assertIn('read-only inventory check: valid', output.getvalue())

    def test_poller_passes_inventory_to_both_plan_builds(self):
        from xl_deploy.poller import BranchPoller, PollCursorStore
        inv = self.inventory(); tx, runner, _router = self.transaction(inv)
        github = SimpleNamespace(latest_sha=lambda branch: SHA, ci_result=lambda branch, sha: 'success')
        runner.changed_paths = lambda base, head: ('xl_qqbot/bot_app/service.py',)
        runner.read_text_at_commit = lambda sha, path: ''
        seen = []
        def execute(plan):
            seen.append(plan)
            return SimpleNamespace(status='completed', message='')
        tx.execute = execute
        poller = BranchPoller(github, runner, lambda branch: tx, PollCursorStore(self.root / 'cursors'), inventory=inv)
        self.assertEqual(poller.poll_branch('test').status, 'deployed')
        self.assertEqual(seen[0].impacted_units, ('xl-test-a.service', 'xl-test-b.service'))

    def test_control_client_sends_instance_ids_and_rejects_unknown_ids(self):
        from xl_deploy.router_client import RouterControlClient
        calls = []
        client = RouterControlClient('http://127.0.0.1:8784', 'token', inventory=self.inventory())
        client._post = lambda path, payload: (calls.append((path, payload)) or {'ok': True, 'results': {'g-a': True}})
        client._get = lambda path: (calls.append((path, None)) or {'drained': True})
        client.pause(('test-a',)); client.announce(('test-a',), '更新完毕', notification_id='stable')
        self.assertTrue(client.drain(('test-b',), 1))
        self.assertEqual(calls[0][1], {'instance_id': 'test-a', 'enabled': True})
        self.assertEqual(calls[1][1]['instance_id'], 'test-a')
        self.assertIn('instance_id=test-b', calls[2][0])
        with self.assertRaises(ValueError): client.pause(('unknown',))

    def test_scalar_groups_and_data_roots_are_invalid(self):
        for field in ('groups', 'data_roots'):
            values = mapping(); values['instances'][0][field] = 'abc'
            with self.assertRaises(ValueError): self.inventory(values)

    def test_config_route_port_and_data_mismatch_fail_before_pause_or_stop(self):
        for case in ('routes', 'port', 'data', 'id', 'missing'):
            with self.subTest(case=case), tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
                previous = self.root; self.root = Path(directory)
                inv = self.inventory(); tx, runner, router = self.transaction(inv)
                file = inv.gateway.config_path if case == 'routes' else inv.instances[0].config_path
                content = file.read_text(encoding='utf-8')
                if case == 'routes': content = content.replace('g-a', 'wrong-group')
                if case == 'port': content = content.replace('8811', '9999')
                if case == 'data': content = content.replace('"data"', '"wrong-data"')
                if case == 'id': content = content.replace('id = "test-a"', 'id = "wrong"')
                if case == 'missing': file.unlink()
                else: file.write_text(content, encoding='utf-8')
                with self.assertRaises(ValueError):
                    tx.execute(plan_deployment('test', SHA, ['xl_qqbot/bot_app/service.py'], inventory=inv))
                self.assertEqual(runner.events, []); self.assertEqual(router.events, [])
                self.root = previous

    def test_backend_only_instance_needs_no_gateway_maintenance(self):
        values = mapping()
        values['instances'].append({'instance_id': 'updater-a', 'watch_ref': 'test',
            'config_path': 'updater/config.toml', 'data_roots': ['updater/data'],
            'units': {'backend': 'xl-updater-a.service'}, 'listen_ports': {'backend': 8890}, 'groups': []})
        inv = config.DeploymentInventory.from_mapping(values, base_dir=self.root)
        plan = plan_deployment('test', SHA, ['xl_updata_server/run_server.py'], inventory=inv)
        self.assertEqual(plan.impacted_units, ('xl-updater-a.service',))
        self.assertEqual(plan.impacted_tiers, ())

    def test_second_instance_preflight_failure_keeps_services_active(self):
        inv = self.inventory(); tx, runner, router = self.transaction(inv)
        original = runner.preflight
        def preflight(plan, candidate, config_path, data_path):
            original(plan, candidate, config_path, data_path)
            if config_path == inv.instances[1].config_path:
                raise RuntimeError('second config failed')
        runner.preflight = preflight
        with self.assertRaises(RuntimeError):
            tx.execute(plan_deployment('test', SHA, ['xl_qqbot/bot_app/service.py'], inventory=inv))
        self.assertFalse(any(e[0] == 'stop' for e in runner.events))
        self.assertEqual(router.events, [])
        self.assertEqual(tx.state.load().phase, 'preflight_failed')

    def test_failed_health_rolls_back_both_instances(self):
        inv = self.inventory(); tx, runner, _router = self.transaction(inv)
        health_count = 0
        def health(units):
            nonlocal health_count
            health_count += 1
            if health_count == 1: raise RuntimeError('test-b not ready')
        runner.health_check = health
        result = tx.execute(plan_deployment('test', SHA, ['xl_qqbot/bot_app/service.py'], inventory=inv))
        self.assertEqual(result.status, 'rolled_back')
        self.assertEqual(tx.state.get_current(current_path=tx.paths.current_path / 'test').name, '1' * 40)
        self.assertEqual([e for e in runner.events if e[0] == 'stop'],
                         [('stop', 'xl-test-a.service', 'xl-test-b.service')] * 2)

    def test_backend_health_uses_instance_config_without_branch_label_gate(self):
        values = mapping()
        values['instances'].append({'instance_id': 'updater-a', 'watch_ref': 'test',
            'config_path': 'updater/config.toml', 'data_roots': ['updater/data'],
            'units': {'backend': 'xl-updater-a.service'}, 'listen_ports': {'backend': 8890}, 'groups': []})
        inv = config.DeploymentInventory.from_mapping(values, base_dir=self.root)
        commands = []
        runner = CommandRunner(inventory=inv, executor=lambda args, **kwargs: commands.append(args))
        runner._wait_for_http_health = lambda request, unit: None
        runner.health_check(('xl-updater-a.service',))
        self.assertIn(str(inv.instances[-1].config_path), commands[1])
        self.assertNotIn('--expect-environment', commands[1])

    def test_empty_forged_plan_is_rejected_before_noop(self):
        inv = self.inventory(); tx, _runner, _router = self.transaction(inv)
        plan = SimpleNamespace(branch='test', sha=SHA, changed_paths=('xl_qqbot/bot_app/service.py',),
                               impacted_units=(), impacted_tiers=(), announcement_scope=())
        with self.assertRaises(ValueError): tx.execute(plan)

    def test_operator_loader_and_legacy_runtime_default(self):
        from xl_deploy.__main__ import load_runtime_config
        from xl_deploy.operator_inventory import load_inventory
        source = Path('xl_deploy/config.toml.example').read_text(encoding='utf-8')
        target = self.root / 'deploy.toml'; target.write_text(source, encoding='utf-8')
        self.assertEqual(load_inventory(target).instances[1].instance_id, 'test-b')
        target.write_text(source.split('[inventory.gateway]')[0], encoding='utf-8')
        self.assertIsNone(load_runtime_config(target).inventory)

    def test_gateway_shared_transport_dependencies_select_gateway(self):
        inv = self.inventory()
        for name in ('sender', 'deploy_control', 'groups', 'tiers', 'updater'):
            with self.subTest(name=name):
                plan = plan_deployment('main', SHA, [f'xl_qqbot/bot_app/{name}.py'], inventory=inv)
                self.assertIn('xl-gateway.service', plan.impacted_units)


    def test_shared_gateway_module_selects_all_matching_consumers(self):
        values = mapping()
        values['instances'][0]['units'].update(notify='xl-a-notify.service', jobs='xl-a-jobs.service')
        values['instances'][0]['listen_ports']['notify'] = 8813
        values['instances'][1]['watch_ref'] = 'main'
        inv = self.inventory(values)
        for branch, expected in (
            ('test', ('xl-test-a.service', 'xl-a-notify.service', 'xl-a-jobs.service')),
            ('main', ('xl-test-b.service', 'xl-gateway.service'))):
            with self.subTest(branch=branch):
                plan = plan_deployment(branch, SHA, ['xl_qqbot/bot_app/gateway.py'], inventory=inv)
                self.assertEqual(plan.impacted_units, expected)
                self.assertEqual(plan.impacted_units.count('xl-gateway.service'), int(branch == 'main'))

    def test_debug_runtime_watch_refs_are_rejected(self):
        for owner in ('instance', 'gateway'):
            with self.subTest(owner=owner):
                values = mapping()
                (values['instances'][0] if owner == 'instance' else values['gateway'])['watch_ref'] = 'debug'
                with self.assertRaisesRegex(ValueError, 'watch_ref'):
                    config.DeploymentInventory.from_mapping(values, base_dir=self.root)

    def test_gateway_data_root_cannot_contain_release_tree(self):
        inv = self.inventory()
        with self.assertRaises(ValueError):
            inv.validate(releases_root=self.root / 'gateway' / 'data' / 'releases')

    def test_gateway_preflight_uses_its_declared_roots(self):
        inv = self.inventory(); tx, runner, _router = self.transaction(inv)
        plan = plan_deployment('main', SHA, ['xl_qqbot/bot_app/gateway.py'], inventory=inv)
        old = tx.paths.releases_root / ('1' * 40)
        tx.state.set_current(old, current_path=tx.paths.current_path / 'main')
        tx.execute(plan)
        self.assertEqual([e[2] for e in runner.events if e[0] == 'preflight'],
                         [self.root / 'gateway' / 'data'])

    def test_gateway_roots_must_be_nonempty_disjoint_and_external(self):
        for roots in ([], 'gateway/data', ['test-a/data'], ['test-a/data/child'],
                      ['gateway'], ['gateway/data', 'gateway/data/child']):
            with self.subTest(roots=roots):
                values = mapping(); values['gateway']['data_roots'] = roots
                with self.assertRaises(ValueError):
                    config.DeploymentInventory.from_mapping(values, base_dir=self.root)
        values = mapping(); values['gateway']['data_roots'] = ['releases/gateway']
        inv = config.DeploymentInventory.from_mapping(values, base_dir=self.root)
        with self.assertRaises(ValueError): inv.validate(releases_root=self.root / 'releases')

    def test_nested_mutable_paths_and_separate_declared_outbox_are_valid(self):
        values = mapping()
        values['instances'][0]['data_roots'].append('test-a/outbox')
        inv = self.inventory(values)
        file = inv.instances[0].config_path
        content = file.read_text(encoding='utf-8').replace('data_dir = "data"', 'data_dir = "data/state"')
        content = content.replace('outbox_dir = "data/outbox"', 'outbox_dir = "outbox"')
        content += '\n[[watch.update_sources]]\nname = "main"\noutbox_dir = "outbox"\n'
        content += '\n[[watch.update_sources]]\nname = "preview"\noutbox_dir = "data/preview"\n'
        file.write_text(content, encoding='utf-8')
        gateway = inv.gateway.config_path
        gateway.write_text(gateway.read_text(encoding='utf-8').replace('data_dir = "data"', 'data_dir = "data/state"'), encoding='utf-8')
        inv.validate_runtime_configs()

    def test_every_update_source_requires_an_owned_outbox(self):
        inv = self.inventory(); file = inv.instances[0].config_path
        original = file.read_text(encoding='utf-8')
        for extra in ('outbox_dir = "../test-b/data"', '', 'outbox_dir = ""', 'outbox_dir = 12'):
            with self.subTest(extra=extra):
                file.write_text(original + '\n[[watch.update_sources]]\nname = "main"\noutbox_dir = "data/outbox"\n'
                                + '\n[[watch.update_sources]]\nname = "preview"\n' + extra + '\n', encoding='utf-8')
                with self.assertRaises(ValueError): inv.validate_runtime_configs()

    def test_gateway_requires_declared_data_roots(self):
        values = mapping()
        values['gateway'].pop('data_roots', None)
        with self.assertRaises(ValueError):
            config.DeploymentInventory.from_mapping(values, base_dir=self.root)

    def test_mutable_path_escape_is_rejected_before_pause_or_stop(self):
        for owner, field in (('bot', 'data_dir'), ('bot', 'outbox_dir'),
                             ('bot', 'source'), ('gateway', 'data_dir')):
            for destination in ('../test-b/data/shared', '../undeclared', '../releases/mutable'):
                with self.subTest(owner=owner, field=field, destination=destination), tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
                    previous = self.root
                    self.root = Path(directory)
                    self.addCleanup(setattr, self, 'root', previous)
                    inv = self.inventory(); tx, runner, router = self.transaction(inv)
                    file = inv.gateway.config_path if owner == 'gateway' else inv.instances[0].config_path
                    content = file.read_text(encoding='utf-8')
                    if field == 'source':
                        content += f'\n[[watch.update_sources]]\nname = "main"\noutbox_dir = "{destination}"\n'
                    elif f'{field} =' in content:
                        original = 'data' if field == 'data_dir' else 'data/outbox'
                        content = content.replace(f'{field} = "{original}"', f'{field} = "{destination}"')
                    else:
                        content += f'\n[watch]\n{field} = "{destination}"\n'
                    file.write_text(content, encoding='utf-8')
                    with self.assertRaises(ValueError):
                        tx.execute(plan_deployment('test', SHA, ['xl_qqbot/bot_app/service.py'], inventory=inv))
                    self.assertFalse(any(e[0] == 'stop' for e in runner.events))
                    self.assertEqual(router.events, [])
                    self.root = previous


if __name__ == '__main__': unittest.main()
