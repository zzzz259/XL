import asyncio
import json
import logging
import os
import shutil
from pathlib import Path

from .config import Config
from .groups import GroupStore
from .outbox import (
    SentRecordStore,
    VersionBatch,
    list_version_batches,
)
from .proactive_outbox import ProactiveOutbox
from .sender import QQSender
from .tiers import GroupTier
from .updater import NoticeStateStore, ServiceMute, read_new_events

_logger = logging.getLogger(__name__)

UPDATE_START_TEXT = "检测到新版本，正在自动更新，期间将暂停服务"


class Watcher:
    def __init__(
        self,
        config: Config,
        sender: QQSender,
        mute: ServiceMute | None = None,
        tiers: GroupTier | None = None,
        proactive_outbox: ProactiveOutbox | None = None,
    ):
        self.config = config
        self.sender = sender
        self.proactive_outbox = proactive_outbox or ProactiveOutbox(
            config.watch.data_dir
        )
        self.store = SentRecordStore(config.watch.data_dir)
        self.group_store = GroupStore(config.watch.data_dir)
        self.mute = mute or ServiceMute()
        self.notice = NoticeStateStore(config.watch.data_dir)
        self.tiers = tiers or GroupTier()
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
        """将更新事件按日志顺序展开为 start -> 对应图片 -> finish。"""
        events_path = Path(self.config.watch.outbox_dir).parent / "update_events.jsonl"
        offset = self.notice.consumed_events
        events, new_offset = read_new_events(events_path, offset)
        batches = {
            batch.version: batch
            for batch in list_version_batches(self.config.watch.outbox_dir)
        }
        consumed = offset
        for event in events:
            if event.event == "start":
                self._enqueue_update_event(event)
                self.mute.muted = True
                _logger.info("已将更新开始通知写入发件箱 version=%s", event.version)
            elif event.event == "finish":
                batch = batches.pop(str(event.version), None)
                if batch is not None:
                    await self._process_batch(batch)
                self._enqueue_update_event(event)
                self.mute.muted = False
                _logger.info(
                    "已将更新结束通知写入发件箱 version=%s new=%d error=%s",
                    event.version,
                    event.new_characters,
                    event.error,
                )
            else:
                _logger.error(
                    "未知游戏更新事件，停止消费以保留日志顺序 line=%s event=%s",
                    event.line_number,
                    event.event,
                )
                break
            consumed = event.line_number
            self.notice.mark_consumed(consumed)

        # Batch output may be visible before its finish event is appended. Queue
        # such remaining batches only after all preceding lifecycle events.
        if consumed == (events[-1].line_number if events else offset):
            for batch in sorted(batches.values(), key=lambda item: item.version):
                await self._process_batch(batch)
            if new_offset > consumed:
                self.notice.mark_consumed(new_offset)

    def _enqueue_update_event(self, event) -> None:
        """Durably enqueue the event for every update-notice-enabled group."""
        group_openids = self.tiers.filter_groups("update_notice", self._target_groups())
        if not group_openids:
            return
        if event.event == "start":
            text = UPDATE_START_TEXT
        elif event.error:
            text = "版本更新失败，服务已恢复，将等待下次自动重试"
        else:
            text = f"版本更新结束，一共有{event.new_characters}个新角色"
        for group_openid in group_openids:
            self.proactive_outbox.enqueue_text(
                self._update_event_key(event), group_openid, 0, text
            )

    async def _process_batch(self, batch: VersionBatch) -> None:
        manifest = self._read_manifest(batch.version_dir)
        self.store.save_manifest(batch.version, manifest)

        group_openids = self._target_groups()
        if not group_openids:
            _logger.warning("没有配置目标群 openid，跳过版本 %s", batch.version)
            return
        # 新角色图鉴推送按 character_push 门禁过滤；批次完成判定只看实际目标群
        group_openids = self.tiers.filter_groups("character_push", group_openids)
        if not group_openids:
            _logger.info(
                "character_push 在全部目标群均未开放，版本 %s 视为无需发送，清理 outbox 目录",
                batch.version,
            )
            shutil.rmtree(batch.version_dir)
            return

        for image in batch.images:
            content = self.config.message.template.format(
                version=batch.version,
                name=image.name,
                file_name=image.file_name,
            )
            event_key = (
                f"game-card:{batch.version}:{image.id or 'unknown'}:{image.file_name}"
            )
            pending_groups = [
                group_openid
                for group_openid in group_openids
                if not self.store.is_sent_to_group(
                    batch.version, image.file_name, group_openid
                )
            ]
            for group_openid in pending_groups:
                self.proactive_outbox.enqueue_image(
                    event_key,
                    group_openid,
                    ordinal=0,
                    source_path=image.file_path,
                    content=content,
                )

        # All target rows and their copied attachments are now durable. The
        # dispatcher owns delivery/retry; only now may the source batch clear.
        shutil.rmtree(batch.version_dir)
        _logger.info(
            "版本 %s 全部图鉴已持久入队 groups=%d images=%d",
            batch.version,
            len(group_openids),
            len(batch.images),
        )

    def _read_manifest(self, version_dir: str) -> dict:
        path = os.path.join(version_dir, "manifest.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    @staticmethod
    def _update_event_key(event) -> str:
        return f"game-update:{event.line_number}:{event.event}:{event.version}"

    def _target_groups(self) -> list[str]:
        manual = self.config.target.group_openids
        if manual:
            return sorted(set(manual))
        return sorted(self.group_store.openids())
