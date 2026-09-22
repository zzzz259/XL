import json
import os
import tempfile

import pytest

from bot_app.config import (
    BotConfig,
    Config,
    MessageConfig,
    TargetConfig,
    UploadConfig,
    WatchConfig,
)
from bot_app.groups import GroupStore
from bot_app.watcher import Watcher


class FakeSender:
    def __init__(self, succeed=True):
        self.succeed = succeed
        self.calls = []

    async def send_image(self, file_path, content, group_openids):
        self.calls.append((file_path, content, group_openids))
        return {g: self.succeed for g in group_openids}

    async def start(self):
        pass

    async def close(self):
        pass


def make_config(outbox_dir, data_dir, groups):
    return Config(
        bot=BotConfig(appid="1", secret="2"),
        watch=WatchConfig(
            outbox_dir=outbox_dir,
            interval_seconds=30,
            data_dir=data_dir,
        ),
        target=TargetConfig(group_openids=groups, auto_learn_from_events=False),
        upload=UploadConfig(file_base_url=""),
        message=MessageConfig(template="【星落】{version}-{name}"),
    )


def make_version(outbox_dir, version, characters):
    vdir = os.path.join(outbox_dir, version)
    os.makedirs(vdir, exist_ok=True)
    manifest = {"version": version, "characters": characters}
    with open(os.path.join(vdir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f)
    for c in characters:
        open(os.path.join(vdir, c["file_name"]), "w").close()
    return vdir


@pytest.mark.asyncio
async def test_watcher_sends_and_deletes():
    with tempfile.TemporaryDirectory() as root:
        outbox_dir = os.path.join(root, "outbox")
        data_dir = os.path.join(root, "data")
        vdir = make_version(
            outbox_dir,
            "v1",
            [{"id": "1", "name": "A", "file_name": "1_A_角色档案_长图.png"}],
        )

        config = make_config(outbox_dir, data_dir, ["g1"])
        sender = FakeSender(succeed=True)
        watcher = Watcher(config, sender)
        await watcher._tick()

        assert len(sender.calls) == 1
        assert sender.calls[0][1] == "【星落】v1-A"
        assert not os.path.exists(vdir)
        assert os.path.exists(os.path.join(data_dir, "sent", "v1", "manifest.json"))
        assert os.path.exists(os.path.join(data_dir, "sent", "v1", "status.json"))


@pytest.mark.asyncio
async def test_watcher_keeps_failed_version():
    with tempfile.TemporaryDirectory() as root:
        outbox_dir = os.path.join(root, "outbox")
        data_dir = os.path.join(root, "data")
        vdir = make_version(
            outbox_dir,
            "v1",
            [{"id": "1", "name": "A", "file_name": "1_A_角色档案_长图.png"}],
        )

        config = make_config(outbox_dir, data_dir, ["g1"])
        sender = FakeSender(succeed=False)
        watcher = Watcher(config, sender)
        await watcher._tick()

        assert len(sender.calls) == 1
        assert os.path.exists(vdir)
        assert not os.path.exists(
            os.path.join(vdir, "1_A_角色档案_长图.png.done")
        )


@pytest.mark.asyncio
async def test_watcher_partial_success_keeps_version():
    with tempfile.TemporaryDirectory() as root:
        outbox_dir = os.path.join(root, "outbox")
        data_dir = os.path.join(root, "data")
        vdir = make_version(
            outbox_dir,
            "v1",
            [{"id": "1", "name": "A", "file_name": "1_A_角色档案_长图.png"}],
        )

        config = make_config(outbox_dir, data_dir, ["g1", "g2"])
        sender = FakeSender(succeed=True)

        async def partial_send(file_path, content, group_openids):
            sender.calls.append((file_path, content, group_openids))
            return {"g1": True, "g2": False}

        sender.send_image = partial_send
        watcher = Watcher(config, sender)
        await watcher._tick()

        assert os.path.exists(vdir)
        store = watcher.store
        assert store.is_sent_to_group("v1", "1_A_角色档案_长图.png", "g1")
        assert not store.is_sent_to_group("v1", "1_A_角色档案_长图.png", "g2")


@pytest.mark.asyncio
async def test_watcher_skips_already_sent_groups():
    with tempfile.TemporaryDirectory() as root:
        outbox_dir = os.path.join(root, "outbox")
        data_dir = os.path.join(root, "data")
        vdir = make_version(
            outbox_dir,
            "v1",
            [{"id": "1", "name": "A", "file_name": "1_A_角色档案_长图.png"}],
        )

        config = make_config(outbox_dir, data_dir, ["g1", "g2"])
        sender = FakeSender(succeed=True)
        watcher = Watcher(config, sender)
        watcher.store.mark_sent_to_group("v1", "1_A_角色档案_长图.png", "g1")
        await watcher._tick()

        assert len(sender.calls) == 1
        assert sender.calls[0][2] == ["g2"]
        # g2 也成功后整个版本完成，outbox 目录会被删除
        assert not os.path.exists(vdir)
        store = watcher.store
        assert store.is_sent_to_group("v1", "1_A_角色档案_长图.png", "g1")
        assert store.is_sent_to_group("v1", "1_A_角色档案_长图.png", "g2")


@pytest.mark.asyncio
async def test_watcher_uses_learned_groups_when_no_manual_list():
    with tempfile.TemporaryDirectory() as root:
        outbox_dir = os.path.join(root, "outbox")
        data_dir = os.path.join(root, "data")
        vdir = make_version(
            outbox_dir,
            "v1",
            [{"id": "1", "name": "A", "file_name": "1_A_角色档案_长图.png"}],
        )

        config = make_config(outbox_dir, data_dir, [])
        sender = FakeSender(succeed=True)
        watcher = Watcher(config, sender)
        watcher.group_store.add("learned_g1", "测试群")
        await watcher._tick()

        assert len(sender.calls) == 1
        assert sender.calls[0][2] == ["learned_g1"]
        assert not os.path.exists(vdir)


@pytest.mark.asyncio
async def test_watcher_manual_override_ignores_learned():
    with tempfile.TemporaryDirectory() as root:
        outbox_dir = os.path.join(root, "outbox")
        data_dir = os.path.join(root, "data")
        vdir = make_version(
            outbox_dir,
            "v1",
            [{"id": "1", "name": "A", "file_name": "1_A_角色档案_长图.png"}],
        )

        config = make_config(outbox_dir, data_dir, ["manual_g1"])
        sender = FakeSender(succeed=True)
        watcher = Watcher(config, sender)
        watcher.group_store.add("learned_g1", "测试群")
        await watcher._tick()

        assert len(sender.calls) == 1
        assert sender.calls[0][2] == ["manual_g1"]


@pytest.mark.asyncio
async def test_watcher_no_groups_known_skips():
    with tempfile.TemporaryDirectory() as root:
        outbox_dir = os.path.join(root, "outbox")
        data_dir = os.path.join(root, "data")
        vdir = make_version(
            outbox_dir,
            "v1",
            [{"id": "1", "name": "A", "file_name": "1_A_角色档案_长图.png"}],
        )

        config = make_config(outbox_dir, data_dir, [])
        sender = FakeSender(succeed=True)
        watcher = Watcher(config, sender)
        await watcher._tick()

        assert len(sender.calls) == 0
        assert os.path.exists(vdir)
