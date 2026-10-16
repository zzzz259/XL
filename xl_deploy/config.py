"""Validated, secret-safe GitHub API configuration."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlparse


@dataclass(frozen=True)
class GitHubConfig:
    owner: str
    repo: str
    token: str = field(repr=False)
    api_url: str = "https://api.github.com"
    timeout_seconds: float = 10.0
    max_pages: int = 3
    per_page: int = 100

    def __post_init__(self) -> None:
        if not self.owner.strip() or "/" in self.owner:
            raise ValueError("GitHub owner must be a non-empty path component")
        if not self.repo.strip() or "/" in self.repo:
            raise ValueError("GitHub repo must be a non-empty path component")
        if not self.token.strip() or "\n" in self.token or "\r" in self.token:
            raise ValueError("GitHub token must be non-empty and single-line")
        parsed_url = urlparse(self.api_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("GitHub API URL must be an absolute HTTP(S) URL")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not 1 <= self.max_pages <= 10:
            raise ValueError("max_pages must be between 1 and 10")
        if not 1 <= self.per_page <= 100:
            raise ValueError("per_page must be between 1 and 100")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> GitHubConfig:
        values = os.environ if environ is None else environ
        repository = values.get("GITHUB_REPOSITORY", "").strip()
        parts = repository.split("/")
        token = values.get("GITHUB_TOKEN", values.get("GH_TOKEN", "")).strip()
        if len(parts) != 2 or not all(parts) or not token:
            raise ValueError("GITHUB_REPOSITORY and GITHUB_TOKEN are required")
        try:
            timeout = float(values.get("XL_DEPLOY_GITHUB_TIMEOUT", "10"))
            max_pages = int(values.get("XL_DEPLOY_GITHUB_MAX_PAGES", "3"))
            per_page = int(values.get("XL_DEPLOY_GITHUB_PER_PAGE", "100"))
        except ValueError as exc:
            raise ValueError("GitHub pagination and timeout settings must be numeric") from exc
        return cls(
            owner=parts[0],
            repo=parts[1],
            token=token,
            api_url=values.get("XL_DEPLOY_GITHUB_API_URL", "https://api.github.com").strip(),
            timeout_seconds=timeout,
            max_pages=max_pages,
            per_page=per_page,
        )

# Inventory is deliberately hand maintained; no service or filesystem discovery.
_ROLES = frozenset({'bot', 'notify', 'jobs', 'backend', 'control'})
_NAME = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}\Z')
_UNIT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.@-]*\.service\Z')


@dataclass(frozen=True)
class InstanceDeployment:
    instance_id: str
    watch_ref: str
    config_path: Path
    data_roots: tuple[Path, ...]
    units: Mapping[str, str]
    listen_ports: Mapping[str, int]
    groups: tuple[str, ...]
    enabled: bool = True


@dataclass(frozen=True)
class GatewayDeployment:
    unit: str
    watch_ref: str
    config_path: Path
    listen_port: int
    data_roots: tuple[Path, ...]


@dataclass(frozen=True)
class DeploymentInventory:
    instances: tuple[InstanceDeployment, ...]
    gateway: GatewayDeployment

    @classmethod
    def from_mapping(cls, values: Mapping, *, base_dir: Path) -> DeploymentInventory:
        def path(value):
            if not isinstance(value, str) or not value.strip():
                raise ValueError('inventory paths must be nonempty strings')
            candidate = Path(value).expanduser()
            return (candidate if candidate.is_absolute() else base_dir / candidate).resolve()
        try:
            instances = []
            if not isinstance(values['instances'], (list, tuple)):
                raise ValueError('instances must be an array')  # noqa: TRY004 - invalid config value
            for item in values['instances']:
                if any(not isinstance(item.get(field), (list, tuple)) for field in ('data_roots', 'groups')):
                    raise ValueError('data_roots and groups must be arrays')
                if set(item) - {'instance_id', 'watch_ref', 'config_path', 'data_roots', 'units', 'listen_ports', 'groups', 'enabled'}:
                    raise ValueError('unknown instance inventory field')
                instances.append(InstanceDeployment(
                    item['instance_id'], item['watch_ref'], path(item['config_path']),
                    tuple(path(p) for p in item['data_roots']),
                    MappingProxyType(dict(item['units'])), MappingProxyType(dict(item['listen_ports'])),
                    tuple(item['groups']), item.get('enabled', True)))
            gateway = values['gateway']
            if set(gateway) != {'unit', 'watch_ref', 'config_path', 'listen_port', 'data_roots'}:
                raise ValueError('invalid gateway inventory fields')
            if not isinstance(gateway['data_roots'], (list, tuple)):
                raise ValueError('gateway data_roots must be an array')  # noqa: TRY004 - invalid config value
            result = cls(tuple(instances), GatewayDeployment(
                gateway['unit'], gateway['watch_ref'], path(gateway['config_path']), gateway['listen_port'],
                tuple(path(p) for p in gateway['data_roots'])))
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError('incomplete or invalid deployment inventory') from exc
        result.validate()
        return result

    @property
    def group_routes(self) -> dict[str, str]:
        return {group: instance.instance_id for instance in self.instances if instance.enabled for group in instance.groups}

    @property
    def allowed_units(self) -> frozenset[str]:
        return frozenset([self.gateway.unit, *(unit for i in self.instances if i.enabled for unit in i.units.values())])

    def owner(self, unit: str) -> tuple[InstanceDeployment | None, str]:
        if unit == self.gateway.unit:
            return None, 'gateway'
        for instance in self.instances:
            if instance.enabled:
                for role, name in instance.units.items():
                    if unit == name:
                        return instance, role
        raise ValueError('systemd unit is not in the deployment inventory allowlist')

    def units_for_ref(self, branch: str) -> tuple[str, ...]:
        return tuple(unit for i in self.instances if i.enabled and i.watch_ref == branch for unit in i.units.values()) + (
            (self.gateway.unit,) if self.gateway.watch_ref == branch else ())

    def validate_runtime_configs(self) -> None:
        """Check declared ownership against external configs before service changes."""
        import tomllib
        def read(path):
            try:
                return tomllib.loads(path.read_text(encoding='utf-8'))
            except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
                raise ValueError('inventory runtime config is missing or invalid') from exc
        def check(condition, label):
            if not condition:
                raise ValueError(f'inventory runtime config mismatch: {label}')
        def owned_path(value, owner, label):
            check(isinstance(value, str) and bool(value.strip()), label)
            candidate = Path(value).expanduser()
            candidate = (candidate if candidate.is_absolute() else owner.config_path.parent / candidate).resolve()
            check(any(candidate.is_relative_to(root.resolve()) for root in owner.data_roots), label)
        gateway = read(self.gateway.config_path)
        owned_path(gateway.get('watch', {}).get('data_dir', './data'), self.gateway, 'gateway data_dir')
        router = gateway.get('router', {})
        check(router.get('group_instances', {}) == self.group_routes, 'gateway group routes')
        check(gateway.get('gateway', {}).get('control_port') == self.gateway.listen_port, 'gateway port')
        expected_ports = {i.instance_id: i.listen_ports['bot'] for i in self.instances
                          if i.enabled and 'bot' in i.units}
        check(router.get('instance_ports', {}) == expected_ports, 'gateway instance ports')
        gateway_settings = gateway.get('gateway', {})
        gateway_control_token = gateway_settings.get('control_token')
        gateway_tokens = gateway_settings.get('instance_tokens', {})
        check(isinstance(gateway_tokens, dict), 'gateway instance tokens')
        check(set(gateway_tokens) == set(expected_ports), 'gateway instance tokens')
        check(all(isinstance(token, str) and len(token) >= 32 for token in gateway_tokens.values()),
              'gateway instance tokens')
        check(len(set(gateway_tokens.values())) == len(gateway_tokens), 'gateway instance tokens')
        check(isinstance(gateway_control_token, str) and len(gateway_control_token) >= 32,
              'gateway control token')
        check(gateway_control_token not in gateway_tokens.values(), 'gateway instance tokens')
        for instance in self.instances:
            if not instance.enabled:
                continue
            values = read(instance.config_path)
            backend = 'backend' in instance.units
            identity = values.get('server', {}).get('instance_id') if backend else values.get('instance', {}).get('id')
            check(identity == instance.instance_id, 'instance_id')
            if not backend and 'bot' in instance.units:
                local_gateway = values.get('gateway', {})
                local_token = local_gateway.get('instance_tokens', {}).get(instance.instance_id)
                check(local_token == gateway_tokens.get(instance.instance_id), 'instance gateway token')
                check(local_gateway.get('control_token') == gateway_control_token, 'gateway control token')
            mutable = values.get('paths' if backend else 'watch', {})
            owned_path(mutable.get('data_dir', 'data' if backend else './data'), instance, 'data_dir')
            if not backend:
                owned_path(mutable.get('outbox_dir'), instance, 'outbox_dir')
                sources = mutable.get('update_sources')
                if sources is not None:
                    check(isinstance(sources, list) and bool(sources), 'update_sources')
                    for source in sources:
                        check(isinstance(source, dict), 'update source')
                        owned_path(source.get('outbox_dir'), instance, 'update source outbox_dir')
            for role, expected_port in instance.listen_ports.items():
                if role == 'backend':
                    api = values.get('api', {})
                    check(api.get('host') == '127.0.0.1' and api.get('enabled') is True, 'backend loopback API')
                    actual = api.get('port')
                elif role == 'control':
                    actual = values.get('router', {}).get('deployment_control_port')
                else:
                    section = 'notification' if role == 'notify' else 'router'
                    actual = values.get(section, {}).get('instance_ports', {}).get(instance.instance_id)
                check(actual == expected_port, f'{role} port')

    def validate(self, *, releases_root: Path | None = None) -> None:
        ids, units, ports, groups = set(), set(), set(), set()
        paths: list[Path] = []
        def unique(value, seen, label):
            if value in seen: raise ValueError(f'duplicate inventory {label}: {value}')
            seen.add(value)
        def port(value):
            if type(value) is not int or not 1 <= value <= 65535: raise ValueError('invalid inventory port')
            unique(value, ports, 'port')
        def unit(value):
            if not isinstance(value, str) or not _UNIT.fullmatch(value): raise ValueError('invalid inventory service unit')
            unique(value, units, 'unit')
        def ref(value):
            if value not in {'test', 'main'}: raise ValueError('unsupported inventory watch_ref; debug is CI-only')
        def external(value):
            value = value.resolve()
            if releases_root is not None and (value.is_relative_to(releases_root.resolve())
                                              or releases_root.resolve().is_relative_to(value)):
                raise ValueError('inventory config/data must remain outside immutable releases')
            if any(value.is_relative_to(other) or other.is_relative_to(value) for other in paths):
                raise ValueError('duplicate or overlapping inventory path')
            paths.append(value)
        ref(self.gateway.watch_ref); unit(self.gateway.unit); port(self.gateway.listen_port)
        external(self.gateway.config_path)
        if not self.gateway.data_roots: raise ValueError('gateway data_roots must be nonempty')
        for value in self.gateway.data_roots: external(value)
        for instance in self.instances:
            if not isinstance(instance.instance_id, str) or not _NAME.fullmatch(instance.instance_id):
                raise ValueError('invalid instance_id')
            unique(instance.instance_id, ids, 'instance_id'); ref(instance.watch_ref)
            if type(instance.enabled) is not bool: raise ValueError('enabled must be boolean')
            if not instance.units or set(instance.units) - _ROLES: raise ValueError('invalid inventory service roles')
            if set(instance.listen_ports) - set(instance.units): raise ValueError('port requires enabled unit role')
            for role in set(instance.units) - {'jobs'}:
                if role not in instance.listen_ports: raise ValueError('HTTP service requires a listen port')
            for name in instance.units.values(): unit(name)
            for value in instance.listen_ports.values(): port(value)
            if not instance.data_roots: raise ValueError('instance data_roots must be nonempty')
            external(instance.config_path)
            for value in instance.data_roots: external(value)
            for group in instance.groups:
                if not isinstance(group, str) or not group.strip() or group != group.strip(): raise ValueError('invalid inventory group')
                unique(group, groups, 'group')
