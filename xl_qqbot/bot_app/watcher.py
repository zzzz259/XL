import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import List, Set

from .config import Config
from .groups import GroupStore
from .outbox import (
    CharacterImage,
    SentRecordStore,
    VersionBatch,
    is_batch_complete,
    is_image_done,
    list_version_batches,
    mark_image_done,
)
from .sender import QQSender
from .updater import NoticeStateStore, ServiceMute, read_new_events

_logger = logging.getLogger(__name__)

UPDATE_START_TEXT = "检测到新版本，正在自动更新，期间将暂停服务"


class Watcher:
    def __init__(self, config: Config, sender: QQSender, mute: ServiceMute | None = None):
        self.config = config
        self.sender = sender
        self.store = SentRecordStore(config.watch.data_dir)
        self.group_store = GroupStore(config.watch.data_dir)
        self.mute = mute or ServiceMute()
        self.notice = NoticeStateStore(config.watch.data_dir)
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except Exception:
                _logger.exception("轮询处理异常")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.config.watch.interval_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        # 顺序保证：先播报"开始" → 再发 outbox 新图 → 最后播报"结束"
        await self._consume_update_events("start")
        batches = list_version_batches(self.config.watch.outbox_dir)
        for batch in batches:
            await self._process_batch(batch)
        await self._consume_update_events("finish", batches)

    async def _consume_update_events(self, phase: str, batches: List[VersionBatch] | None = None) -> None:
        """按序消费更新事件流水；本阶段不匹配的事件留给下一阶段，保证播报顺序。

        事件由服务器处理器按序追加（start 在前 finish 在后），事件持久化在
        update_events.jsonl，比轮询间隔更短的更新也不会漏报。
        """
        events_path = Path(self.config.watch.outbox_dir).parent / "update_events.jsonl"
        offset = self.notice.consumed_events
        events, _ = read_new_events(events_path, offset)
        if not events:
            return
        group_openids = self._target_groups()
        consumed = offset
        for event in events:
            if event.event != phase:
                break  # 留给下一阶段（事件有序，遇到别的类型即停）
            if not group_openids:
                consumed += 1
                continue
            if phase == "start":
                for group_openid in group_openids:
                    await self.sender.send_text(group_openid, UPDATE_START_TEXT)
                self.mute.muted = True
                _logger.info("已播报更新开始 version=%s", event.version)
                consumed += 1
            else:
                # 更新结束语必须等本批 outbox 图全部发完（用户要求"等新角色发完"）
                if any(not is_batch_complete(b) for b in (batches or [])):
                    break
                if event.error:
                    text = "版本更新失败，服务已恢复，将等待下次自动重试"
                else:
                    text = f"版本更新结束，一共有{event.new_characters}个新角色"
                for group_openid in group_openids:
                    await self.sender.send_text(group_openid, text)
                self.mute.muted = False
                _logger.info("已播报更新结束 version=%s new=%d error=%s", event.version, event.new_characters, event.error)
                consumed += 1
        if consumed != offset:
            self.notice.mark_consumed(consumed)

    async def _process_batch(self, batch: VersionBatch) -> None:
        manifest = self._read_manifest(batch.version_dir)
        self.store.save_manifest(batch.version, manifest)

        group_openids = self._target_groups()
        if not group_openids:
            _logger.warning("没有配置目标群 openid，跳过版本 %s", batch.version)
            return

        for image in batch.images:
            if is_image_done(batch.version_dir, image.file_name):
                continue
            await self._send_image(batch.version, image, group_openids)

        if is_batch_complete(batch):
            shutil.rmtree(batch.version_dir)
            _logger.info("版本 %s 全部分发完成，已删除 outbox 目录", batch.version)

    def _read_manifest(self, version_dir: str) -> dict:
        path = os.path.join(version_dir, "manifest.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    async def _send_image(
        self,
        version: str,
        image: CharacterImage,
        group_openids: List[str],
    ) -> None:
        content = self.config.message.template.format(
            version=version,
            name=image.name,
            file_name=image.file_name,
        )
        pending_groups = [
            g
            for g in group_openids
            if not self.store.is_sent_to_group(version, image.file_name, g)
        ]
        if not pending_groups:
            mark_image_done(image.version_dir, image.file_name)
            return

        results = await self.sender.send_image(
            image.file_path, content, pending_groups
        )

        all_ok = True
        for group_openid, success in results.items():
            if success:
                self.store.mark_sent_to_group(version, image.file_name, group_openid)
            else:
                all_ok = False

        if all_ok:
            mark_image_done(image.version_dir, image.file_name)

    def _target_groups(self) -> List[str]:
        manual = self.config.target.group_openids
        if manual:
            return sorted(set(manual))
        return sorted(self.group_store.openids())
