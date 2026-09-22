import asyncio
import hashlib
import logging
import os
from typing import Dict, List

import aiohttp
from botpy.http import BotHttp, Route
from botpy.robot import Token

from .config import Config

logger = logging.getLogger(__name__)


class QQSender:
    """封装 qq-botpy 的 HTTP API，完成群图片上传与发送。

    当前实现包含两条上传路径：
    1. URL 直传：配置 [upload].file_base_url 后，把本地路径映射为公网 URL，
       调用 SDK 对应接口 POST /v2/groups/{group_openid}/files（json: file_type=1, url=...）。
    2. 分片上传：未配置 file_base_url 时，按官方文档走
       upload_prepare -> 分片 PUT -> upload_part_finish -> files 合并。
       该路径未在真实环境验证，需在填入凭据后实测。

    发送消息使用 POST /v2/groups/{group_openid}/messages，msg_type=7，
    media 字段携带 file_info；文字放在 content 中随消息一起发出。
    """

    def __init__(self, config: Config):
        self.config = config
        self.http = BotHttp(
            timeout=120,
            app_id=config.bot.appid,
            secret=config.bot.secret,
        )
        self._seq_counter: Dict[str, int] = {}

    async def start(self) -> None:
        """登录并校验凭据。"""
        token = Token(
            app_id=self.config.bot.appid,
            secret=self.config.bot.secret,
        )
        await self.http.login(token)

    async def close(self) -> None:
        await self.http.close()

    async def send_image(
        self,
        file_path: str,
        content: str,
        group_openids: List[str],
        reply_to: str = "",
    ) -> Dict[str, bool]:
        """向多个群发送同一张图片，返回每个群的成功状态。

        reply_to 传入触发消息的 msg_id 时为被动回复（引用原消息），
        被动回复在任何群都有权限，不受主动消息权限的群范围限制。
        """
        results: Dict[str, bool] = {}
        for group_openid in group_openids:
            results[group_openid] = await self._send_to_group(file_path, content, group_openid, reply_to)
        return results

    async def _send_to_group(
        self,
        file_path: str,
        content: str,
        group_openid: str,
        reply_to: str = "",
    ) -> bool:
        for attempt in range(2):
            try:
                media = await self._upload_group_file(group_openid, file_path)
                await self._send_group_media(group_openid, content, media, reply_to)
                logger.info("发送图片成功 group=%s file=%s", group_openid, os.path.basename(file_path))
                return True
            except Exception as error:
                logger.warning(
                    "发送图片失败(第%d次) group=%s file=%s error=%s",
                    attempt + 1, group_openid, os.path.basename(file_path), error,
                )
                if attempt == 1:
                    return False
                await asyncio.sleep(1)
        return False

    async def _upload_group_file(self, group_openid: str, file_path: str) -> Dict[str, str]:
        if self.config.upload.file_base_url:
            url = self._to_public_url(file_path)
            route = Route(
                "POST",
                "/v2/groups/{group_openid}/files",
                group_openid=group_openid,
            )
            result = await self.http.request(
                route,
                json={
                    "file_type": 1,
                    "url": url,
                    "srv_send_msg": False,
                },
            )
            return {"file_info": result["file_info"]}
        return await self._upload_by_multipart(group_openid, file_path)

    def _to_public_url(self, file_path: str) -> str:
        base = self.config.upload.file_base_url.rstrip("/")
        outbox = self.config.watch.outbox_dir.rstrip("/")
        rel = os.path.relpath(file_path, outbox)
        rel = rel.replace(os.sep, "/")
        return f"{base}/{rel}"

    async def _upload_by_multipart(
        self,
        group_openid: str,
        file_path: str,
    ) -> Dict[str, str]:
        file_size = os.path.getsize(file_path)
        file_name = os.path.basename(file_path)
        md5_full, sha1_full, md5_10m = self._compute_hashes(file_path)

        route = Route(
            "POST",
            "/v2/groups/{group_id}/upload_prepare",
            group_id=group_openid,
        )
        prepare = await self.http.request(
            route,
            json={
                "file_type": 1,
                "file_size": str(file_size),
                "file_name": file_name,
                "md5": md5_full,
                "sha1": sha1_full,
                "md5_10m": md5_10m,
            },
        )
        upload_id = prepare["upload_id"]
        block_size = int(prepare["block_size"])
        parts = prepare["parts"]

        async with aiohttp.ClientSession() as session:
            with open(file_path, "rb") as f:
                for part in parts:
                    index = part["index"]
                    presigned_url = part["presigned_url"]
                    chunk_size = int(part.get("block_size", block_size))
                    chunk = f.read(chunk_size)
                    md5_chunk = hashlib.md5(chunk).hexdigest()

                    async with session.put(presigned_url, data=chunk) as resp:
                        if resp.status not in (200, 202, 204):
                            text = await resp.text()
                            raise RuntimeError(
                                f"分片 PUT 失败: {resp.status} {text[:200]}"
                            )

                    finish_route = Route(
                        "POST",
                        "/v2/groups/{group_id}/upload_part_finish",
                        group_id=group_openid,
                    )
                    await self.http.request(
                        finish_route,
                        json={
                            "upload_id": upload_id,
                            "part_index": index,
                            "block_size": str(len(chunk)),
                            "md5": md5_chunk,
                        },
                    )

        route = Route(
            "POST",
            "/v2/groups/{group_openid}/files",
            group_openid=group_openid,
        )
        result = await self.http.request(
            route,
            json={
                "file_type": 1,
                "upload_id": upload_id,
                "srv_send_msg": False,
            },
        )
        return {"file_info": result["file_info"]}

    @staticmethod
    def _compute_hashes(file_path: str):
        md5_full = hashlib.md5()
        sha1_full = hashlib.sha1()
        md5_10m = hashlib.md5()
        limit = 10_002_432
        counted = 0
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                md5_full.update(chunk)
                sha1_full.update(chunk)
                if counted < limit:
                    take = min(len(chunk), limit - counted)
                    md5_10m.update(chunk[:take])
                    counted += take
        return md5_full.hexdigest(), sha1_full.hexdigest(), md5_10m.hexdigest()

    async def _send_group_media(
        self,
        group_openid: str,
        content: str,
        media: Dict[str, str],
        reply_to: str = "",
    ) -> None:
        seq = self._seq_counter.get(group_openid, 0) + 1
        self._seq_counter[group_openid] = seq
        route = Route(
            "POST",
            "/v2/groups/{group_openid}/messages",
            group_openid=group_openid,
        )
        payload = {
            "msg_type": 7,
            "media": {"file_info": media["file_info"]},
            "msg_seq": seq,
        }
        # content 留空即只发图不带文字
        if content:
            payload["content"] = content
        if reply_to:
            payload["msg_id"] = reply_to
        await self.http.request(route, json=payload)

    async def send_text(self, group_openid: str, text: str, reply_to: str = "") -> bool:
        """发送纯文本消息，完整记录发送结果日志。

        reply_to 传入触发消息的 msg_id 时为被动回复（任何群都有权限）。
        """
        seq = self._seq_counter.get(group_openid, 0) + 1
        self._seq_counter[group_openid] = seq
        route = Route(
            "POST",
            "/v2/groups/{group_openid}/messages",
            group_openid=group_openid,
        )
        payload = {"msg_type": 0, "content": text, "msg_seq": seq}
        if reply_to:
            payload["msg_id"] = reply_to
        try:
            await self.http.request(route, json=payload)
            logger.info("发送文字成功 group=%s text=%r", group_openid, text[:80])
            return True
        except Exception as error:
            # 发送失败必须可见，静默吞掉会让运维完全失明
            logger.error("发送文字失败 group=%s text=%r error=%s", group_openid, text[:80], error)
            return False
