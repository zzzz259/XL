"""Single router-owned dispatcher for proactive QQ notifications."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from .proactive_outbox import OutboxMessage, ProactiveOutbox

_logger = logging.getLogger(__name__)
_QUIET_TIMEZONE = ZoneInfo("Asia/Shanghai")
_POLL_SECONDS = 1.0
_RETRY_BASE_SECONDS = 30
_RETRY_MAX_SECONDS = 3600


def is_quiet_hours(now: datetime | None = None) -> bool:
    """Whether proactive delivery is blocked at this Asia/Shanghai instant."""
    current = now or datetime.now(_QUIET_TIMEZONE)
    if current.tzinfo is None:
        raise ValueError("quiet-hours clock must return an aware datetime")
    local_time = current.astimezone(_QUIET_TIMEZONE).time()
    return (
        local_time.hour,
        local_time.minute,
        local_time.second,
        local_time.microsecond,
    ) >= (2, 0, 0, 0) and (
        local_time.hour,
        local_time.minute,
        local_time.second,
        local_time.microsecond,
    ) < (8, 0, 0, 0)


class ProactiveDispatcher:
    """Send durable outbox rows, with FIFO enforced independently per group."""

    def __init__(self, outbox: ProactiveOutbox, sender, *, now=None):
        self.outbox = outbox
        self.sender = sender
        self._now = now or (lambda: datetime.now(_QUIET_TIMEZONE))

    async def dispatch_once(self) -> int:
        """Attempt each currently eligible group head once; return successful sends."""
        current = self._now()
        if is_quiet_hours(current):
            return 0

        messages = self.outbox.next_due_heads(now=current.timestamp())
        sent_count = 0
        for message in messages:
            # A prior network operation can cross 02:00. Recheck immediately
            # before every QQ API request, not only at the top of the loop.
            send_time = self._now()
            if is_quiet_hours(send_time):
                break
            try:
                delivered = await self._send(message)
            except Exception:
                _logger.exception(
                    "主动消息发送异常 sequence=%s group=%s",
                    message.sequence,
                    message.recipient,
                )
                delivered = False

            if delivered:
                # If this durable acknowledgement fails, stop dispatching. The
                # row remains pending and at-least-once retry is safer than loss.
                self.outbox.mark_sent(message.sequence)
                sent_count += 1
            else:
                retry_at = self._retry_time(message, self._now())
                self.outbox.mark_failed(message.sequence, retry_at)
        return sent_count

    async def _send(self, message: OutboxMessage) -> bool:
        if message.kind == "text":
            return (
                await self.sender.send_text(message.recipient, message.text or "")
                is True
            )
        if message.kind == "image":
            if not message.media_path:
                raise RuntimeError("queued image is missing its durable spool path")
            media_path = self.outbox.data_dir / message.media_path
            results = await self.sender.send_image(
                str(media_path), message.text or "", [message.recipient]
            )
            return isinstance(results, dict) and results.get(message.recipient) is True
        raise RuntimeError(f"unsupported proactive message kind: {message.kind}")

    @staticmethod
    def _retry_time(message: OutboxMessage, now: datetime) -> float:
        delay = min(
            _RETRY_BASE_SECONDS * (2 ** min(message.retry_count, 10)),
            _RETRY_MAX_SECONDS,
        )
        return now.timestamp() + delay

    async def run(self, stop_event: asyncio.Event) -> None:
        """Keep delivery active while monitors run; only the final sender gates time."""
        while not stop_event.is_set():
            try:
                await self.dispatch_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("主动发件箱处理失败；保持队列待发送并稍后重试")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass
