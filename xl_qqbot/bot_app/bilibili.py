"""B 站 UP 主动态监视：图文动态 + 视频投稿两路轮询，支持多目标与两种模式。

接口事实（已在服务器实测全通，均需登录态 SESSDATA）：
- 图文列表 GET /x/polymer/web-dynamic/v1/opus/feed/space?host_mid=<mid>（无需签名），
  每项 opus_id 为雪花 ID，数字越大越新，可直接当排序/去重键；pub_time 为空，
  时间要从详情拿。
- 图文详情 GET /x/polymer/web-dynamic/v1/opus/detail?id=<opus_id>（无需签名），
  data.item.modules 是列表，按 module_type 取标题/作者时间/正文与图片段落。
- 视频列表 GET /x/space/arc/search?mid=<mid>&ps=5（需 WBI 签名），
  data.list.vlist[] 含 bvid/title/created/author。
- UP 主名 GET /x/space/acc/info?mid=<mid>（需 WBI 签名），取 data.name。
- 旧的 feed/space 动态接口在机房 IP 上永远 412（带 SESSDATA 也无效），已弃用。
- 风控/未登录返回非 0 code 或 HTTP 412：记 warning 跳过该路本轮，绝不能崩。

模式：
- full（官方号）：图文动态发全文+逐张原图；视频发一行通知。
- notice（普通 UP 主）：图文和视频都只发一行通知，不拉详情、不下图。
通知格式统一为：你关注的<名字>更新啦：<标题>\\n<链接>
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp

from .config import DEFAULT_BILI_MID, BilibiliTarget, Config
from .groups import GroupStore
from .tiers import GroupTier

_logger = logging.getLogger(__name__)

OPUS_FEED_URL = "https://api.bilibili.com/x/polymer/web-dynamic/v1/opus/feed/space"
OPUS_DETAIL_URL = "https://api.bilibili.com/x/polymer/web-dynamic/v1/opus/detail"
ARC_SEARCH_URL = "https://api.bilibili.com/x/space/arc/search"
ACC_INFO_URL = "https://api.bilibili.com/x/space/acc/info"
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"

TEXT_PREFIX = "【星落官方动态】"
MAX_PER_ROUND = 5  # 每路每目标每轮最多推送条数，超出留到下一轮，防历史洪水

# WBI 混合密钥表（官方算法，取拼接 key 按表重排后的前 32 位）
MixinKeyTab = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

MOBILE_CHROME_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36"
)


class BilibiliApiError(Exception):
    """B 站接口返回异常（HTTP 412 / code 非 0 / 响应非 JSON 等）。"""


def _now() -> int:
    return int(time.time())


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _mixin_key(orig: str) -> str:
    return "".join(orig[i] for i in MixinKeyTab)[:32]


def _key_from_wbi_url(url: str) -> str:
    # https://i0.hdslb.com/bfs/wbi/<key>.png -> <key>
    name = os.path.basename(urllib.parse.urlparse(url).path)
    return os.path.splitext(name)[0]


class BilibiliClient:
    """多目标轮询客户端（mid 由各方法传入）：cookie 注入 + WBI 签名 + 图片下载。"""

    def __init__(
        self,
        sessdata: str = "",
        session: Optional[aiohttp.ClientSession] = None,
    ):
        self.sessdata = sessdata or ""
        self._session = session
        self._wbi_keys: Optional[tuple[str, str]] = None

    def _headers(self, mid: Optional[int] = None) -> Dict[str, str]:
        headers = {"User-Agent": MOBILE_CHROME_UA}
        if mid is not None:
            headers["Referer"] = f"https://m.bilibili.com/space/{mid}"
        if self.sessdata:
            headers["Cookie"] = f"SESSDATA={self.sessdata}"
        return headers

    async def _ensure_session(self) -> None:
        if self._session is None:
            self._session = aiohttp.ClientSession()

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def fetch_opus_feed(self, mid: int) -> List[dict]:
        """图文动态列表（无需签名），失败抛 BilibiliApiError。"""
        data = await self._get_json(OPUS_FEED_URL, {"host_mid": str(mid)}, mid)
        items = data.get("items")
        if not isinstance(items, list):
            return []
        return [i for i in items if isinstance(i, dict)]

    async def fetch_opus_detail(self, opus_id) -> dict:
        """图文动态详情（无需签名），返回 data.item；失败抛 BilibiliApiError。"""
        data = await self._get_json(OPUS_DETAIL_URL, {"id": str(opus_id)}, None)
        item = data.get("item")
        if not isinstance(item, dict):
            raise BilibiliApiError(f"opus 详情缺少 item: opus={opus_id}")
        return item

    async def fetch_videos(self, mid: int) -> List[dict]:
        """视频投稿列表（WBI 签名，ps=5 按最新排序），失败抛 BilibiliApiError。"""
        params = await self._signed_params({"mid": str(mid), "ps": "5"})
        data = await self._get_json(ARC_SEARCH_URL, params, mid)
        vlist = ((data.get("list") or {}).get("vlist"))
        if not isinstance(vlist, list):
            return []
        return [v for v in vlist if isinstance(v, dict)]

    async def fetch_acc_info(self, mid: int) -> dict:
        """UP 主信息（WBI 签名），取 data.name 用；失败抛 BilibiliApiError。"""
        params = await self._signed_params({"mid": str(mid)})
        return await self._get_json(ACC_INFO_URL, params, mid)

    async def download_image(
        self, url: str, dest_dir: str, name: str = "image", mid: Optional[int] = None
    ) -> str:
        """下载原图到 dest_dir，返回本地路径；失败抛 BilibiliApiError。"""
        await self._ensure_session()
        os.makedirs(dest_dir, exist_ok=True)
        clean = url.split("?")[0]
        ext = os.path.splitext(urllib.parse.urlparse(clean).path)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            ext = ".jpg"
        path = os.path.join(dest_dir, f"{name}{ext}")
        async with self._session.get(url, headers=self._headers(mid)) as resp:
            if resp.status != 200:
                raise BilibiliApiError(f"下载图片 HTTP {resp.status}: {url}")
            content = await resp.read()
        with open(path, "wb") as f:
            f.write(content)
        return path

    async def _wbi_key_pair(self) -> tuple[str, str]:
        if self._wbi_keys is None:
            data = await self._get_json(NAV_URL, None, None)
            wbi_img = data.get("wbi_img") or {}
            img_key = _key_from_wbi_url(str(wbi_img.get("img_url") or ""))
            sub_key = _key_from_wbi_url(str(wbi_img.get("sub_url") or ""))
            if not img_key or not sub_key:
                raise BilibiliApiError("nav 接口未返回 wbi_img")
            self._wbi_keys = (img_key, sub_key)
        return self._wbi_keys

    async def _signed_params(self, params: Dict[str, str]) -> Dict[str, str]:
        img_key, sub_key = await self._wbi_key_pair()
        mixin_key = _mixin_key(img_key + sub_key)
        signed = {k: v for k, v in params.items() if v is not None}
        signed["wts"] = _now()
        query = urllib.parse.urlencode(sorted(signed.items()))
        signed["w_rid"] = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
        return signed

    async def _get_json(
        self, url: str, params: Optional[Dict[str, str]], mid: Optional[int] = None
    ) -> dict:
        await self._ensure_session()
        async with self._session.get(url, params=params, headers=self._headers(mid)) as resp:
            body = await resp.text()
        if resp.status != 200:
            raise BilibiliApiError(f"HTTP {resp.status}: {body[:120]}")
        try:
            payload = json.loads(body)
        except ValueError:
            raise BilibiliApiError(f"响应不是 JSON（HTTP {resp.status}）: {body[:120]}")
        if not isinstance(payload, dict):
            raise BilibiliApiError(f"响应格式异常: {body[:120]}")
        code = payload.get("code", -1)
        if code != 0:
            raise BilibiliApiError(f"code={code} message={payload.get('message')!r}")
        data = payload.get("data")
        return data if isinstance(data, dict) else {}


# ---------- 解析（全部防御式，失败返回 None 由调用方记 warning 跳过） ----------

def parse_opus_feed_item(item) -> Optional[dict]:
    """图文 feed 单条解析：取 opus_id（强制 int）/标题/封面/跳转链接。"""
    if not isinstance(item, dict):
        _logger.warning("图文 feed 条目不是对象，已跳过: %r", item)
        return None
    raw_id = item.get("opus_id")
    try:
        opus_id = int(raw_id)
    except (TypeError, ValueError):
        _logger.warning("图文 feed 条目缺少有效 opus_id，已跳过: %r", raw_id)
        return None
    return {
        "opus_id": opus_id,
        "title": str(item.get("content") or "").strip(),
        "cover": str((item.get("cover") or {}).get("url") or ""),
        "jump_url": str(item.get("jump_url") or ""),
    }


def _paragraph_words(para: dict) -> str:
    nodes = (para.get("text") or {}).get("nodes")
    if not isinstance(nodes, list):
        return ""
    parts = []
    for node in nodes:
        if isinstance(node, dict):
            word = str((node.get("word") or {}).get("words") or "")
            if word:
                parts.append(word)
    return "".join(parts)


def _paragraph_pics(para: dict) -> List[str]:
    pics = (para.get("pic") or {}).get("pics")
    if not isinstance(pics, list):
        return []
    return [
        str(p.get("url") or "")
        for p in pics
        if isinstance(p, dict) and p.get("url")
    ]


def parse_opus_detail(item, opus_id=None) -> Optional[dict]:
    """图文详情解析为统一结构 {id_str, text(标题+正文), pics, pub_ts, link}。

    modules 是列表，按 module_type 找标题/作者/正文，其余类型忽略。
    """
    if not isinstance(item, dict):
        _logger.warning("图文详情不是对象，已跳过: opus=%s", opus_id)
        return None
    if _safe_int(item.get("type"), 0) != 0:
        _logger.warning("图文详情 type=%s 非 0（非图文），已跳过: opus=%s", item.get("type"), opus_id)
        return None
    id_str = str(item.get("id_str") or ("" if opus_id is None else str(opus_id))).strip()
    if not id_str:
        _logger.warning("图文详情缺少 id_str，已跳过: opus=%s", opus_id)
        return None
    modules = item.get("modules")
    if not isinstance(modules, list):
        _logger.warning("图文详情 modules 结构异常，已跳过: opus=%s", opus_id)
        return None

    title = ""
    author = ""
    pub_ts = 0
    paragraphs: List[str] = []
    pics: List[str] = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        module_type = module.get("module_type")
        if module_type == "MODULE_TYPE_TITLE":
            title = str((module.get("module_title") or {}).get("text") or "").strip()
        elif module_type == "MODULE_TYPE_AUTHOR":
            author_info = module.get("module_author") or {}
            author = str(author_info.get("name") or "").strip()
            pub_ts = _safe_int(author_info.get("pub_ts"), 0)
        elif module_type == "MODULE_TYPE_CONTENT":
            paras = (module.get("module_content") or {}).get("paragraphs")
            if not isinstance(paras, list):
                continue
            for para in paras:
                if not isinstance(para, dict):
                    continue
                if str(para.get("para_type")) == "1":
                    words = _paragraph_words(para)
                    if words:
                        paragraphs.append(words)
                elif str(para.get("para_type")) == "2":
                    pics.extend(_paragraph_pics(para))
        # 其他 module_type 忽略

    body = "\n".join(paragraphs)
    text = title + ("\n" + body if body else "")
    return {
        "id_str": id_str,
        "title": title,
        "text": text,
        "pics": pics,
        "pub_ts": pub_ts,
        "link": f"https://www.bilibili.com/opus/{id_str}",
        "author": author,
    }


def parse_video_item(item) -> Optional[dict]:
    """视频列表单条解析：取 bvid/标题/发布时间（秒）。"""
    if not isinstance(item, dict):
        _logger.warning("视频条目不是对象，已跳过: %r", item)
        return None
    bvid = str(item.get("bvid") or "").strip()
    if not bvid:
        _logger.warning("视频条目缺少 bvid，已跳过: %r", item.get("aid"))
        return None
    return {
        "bvid": bvid,
        "title": str(item.get("title") or "").strip(),
        "created": _safe_int(item.get("created"), 0),
    }


# ---------- 推送文字 ----------

def format_opus_text(dyn: dict) -> str:
    """full 模式图文推送文字：前缀 + 标题\\n正文；无文字内容返回空串（只发图）。"""
    text = (dyn.get("text") or "").strip()
    return f"{TEXT_PREFIX}{text}" if text else ""


def format_notice_text(name: str, title: str, link: str) -> str:
    """两路统一的通知格式（notice 模式全部、full 模式的视频）。"""
    return f"你关注的{name}更新啦：{title}\n{link}"


def opus_link(opus_id) -> str:
    return f"https://www.bilibili.com/opus/{opus_id}"


def video_link(bvid: str) -> str:
    return f"https://www.bilibili.com/video/{bvid}"


# ---------- 状态（按目标分键，兼容迁移旧版顶层键） ----------

class TargetState:
    """单个 UP 主的持久化状态视图（基线用键存在性判断，未设过即首轮）。"""

    def __init__(self, store: "BilibiliStateStore", mid: str):
        self._store = store
        self._mid = str(mid)

    @property
    def _entry(self) -> dict:
        return self._store._state["targets"][self._mid]

    @property
    def has_opus_baseline(self) -> bool:
        return "last_opus_id" in self._entry

    @property
    def last_opus_id(self) -> int:
        return _safe_int(self._entry.get("last_opus_id"), 0)

    def is_newer_opus(self, opus_id) -> bool:
        return int(opus_id) > self.last_opus_id

    def mark_opus(self, opus_id) -> None:
        self._entry["last_opus_id"] = int(opus_id)
        self._store._save()

    @property
    def has_video_baseline(self) -> bool:
        return "last_bvid" in self._entry

    @property
    def last_video_created(self) -> int:
        return _safe_int(self._entry.get("last_video_created"), 0)

    @property
    def last_bvid(self) -> str:
        return str(self._entry.get("last_bvid") or "")

    def is_newer_video(self, created, bvid) -> bool:
        return (_safe_int(created), str(bvid)) > (self.last_video_created, self.last_bvid)

    def mark_video(self, created, bvid) -> None:
        self._entry["last_video_created"] = _safe_int(created)
        self._entry["last_bvid"] = str(bvid)
        self._store._save()

    @property
    def name(self) -> str:
        return str(self._entry.get("name") or "")

    def set_name(self, name: str) -> None:
        self._entry["name"] = str(name)
        self._store._save()


class BilibiliStateStore:
    """两路去重状态按目标分键：{"targets": {"<mid>": {...}}}，原子写入。

    兼容迁移：发现旧版顶层 last_opus_id/last_video_created/last_bvid 时
    迁入 targets[str(DEFAULT_BILI_MID)]，保留已发基线（首轮不重发语义不变）。
    """

    def __init__(self, data_dir: str):
        self._path = Path(data_dir) / "bilibili_state.json"
        self._state = self._load()
        if self._migrate():
            self._save()

    def _load(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _migrate(self) -> bool:
        state = self._state
        if isinstance(state.get("targets"), dict):
            changed = False
            for key in ("last_opus_id", "last_video_created", "last_bvid",
                        "last_id_str", "last_pub_ts"):
                if key in state:
                    state.pop(key)
                    changed = True
            return changed
        targets: Dict[str, dict] = {}
        legacy_keys = ("last_opus_id", "last_video_created", "last_bvid")
        if any(key in state for key in legacy_keys):
            entry = {key: state[key] for key in legacy_keys if key in state}
            targets[str(DEFAULT_BILI_MID)] = entry
        self._state = {"targets": targets}
        return True

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".part")
        temporary.write_text(json.dumps(self._state, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self._path)

    def target(self, mid) -> TargetState:
        targets = self._state.setdefault("targets", {})
        key = str(mid)
        entry = targets.get(key)
        if not isinstance(entry, dict):
            entry = {}
            targets[key] = entry
        return TargetState(self, key)


class BilibiliWatcher:
    """多目标两路轮询（图文 + 视频）并推送，模式与 Watcher 相同。"""

    def __init__(self, config: Config, sender, tiers: GroupTier | None = None):
        self.config = config
        self.sender = sender
        self.group_store = GroupStore(config.watch.data_dir)
        self.state = BilibiliStateStore(config.watch.data_dir)
        self.tiers = tiers or GroupTier()
        self.tmp_dir = os.path.join(config.watch.data_dir, "bili_tmp")
        self._client: Optional[BilibiliClient] = None
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def _get_client(self) -> BilibiliClient:
        if self._client is None:
            self._client = BilibiliClient(self.config.bilibili.sessdata)
        return self._client

    async def run(self) -> None:
        if not self.config.bilibili.enabled:
            _logger.info("B 站动态监视未启用（[bilibili].enabled=false）")
            return
        _logger.info(
            "B 站动态监视启动 targets=%d interval=%ss",
            len(self.config.bilibili.targets), self.config.bilibili.interval_seconds,
        )
        try:
            while not self._stop_event.is_set():
                try:
                    await self._tick()
                except Exception:
                    _logger.exception("B 站动态轮询异常")
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.config.bilibili.interval_seconds,
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            if self._client is not None:
                await self._client.close()

    async def _tick(self) -> None:
        client = self._get_client()
        for target in self.config.bilibili.targets:
            state = self.state.target(target.mid)
            await self._tick_opus(client, target, state)
            await self._tick_videos(client, target, state)

    # ---------- 名字解析 ----------

    async def _resolve_name(
        self, client, target: BilibiliTarget, state: TargetState, videos: Optional[List[dict]] = None
    ) -> str:
        """display 名优先级：配置 name → state 缓存 → acc/info → vlist author → mid 字符串。

        网络解析成功的结果写入 state 缓存避免每轮请求；mid 兜底不缓存（下轮重试）。
        """
        if target.name:
            return target.name
        if state.name:
            return state.name
        resolved = ""
        try:
            info = await client.fetch_acc_info(target.mid)
            if isinstance(info, dict):
                resolved = str(info.get("name") or "").strip()
        except BilibiliApiError as error:
            _logger.warning("UP 主名解析 acc/info 失败 mid=%s: %s", target.mid, error)
        if not resolved and videos:
            for item in videos:
                if isinstance(item, dict):
                    author = str(item.get("author") or "").strip()
                    if author:
                        resolved = author
                        break
        if not resolved:
            _logger.warning("UP 主名解析失败 mid=%s，本轮回退用 mid 字符串", target.mid)
            return str(target.mid)
        state.set_name(resolved)
        return resolved

    # ---------- 图文路 ----------

    async def _tick_opus(self, client, target: BilibiliTarget, state: TargetState) -> None:
        try:
            items = await client.fetch_opus_feed(target.mid)
        except BilibiliApiError as error:
            _logger.warning("B 站图文列表请求失败 mid=%s，跳过本轮图文路: %s", target.mid, error)
            return
        entries = [e for e in (parse_opus_feed_item(i) for i in items) if e is not None]
        if not entries:
            return

        if not state.has_opus_baseline:
            # 首次运行以当前最新一条为基线，不补发历史
            baseline = max(e["opus_id"] for e in entries)
            state.mark_opus(baseline)
            _logger.info("图文路首次轮询 mid=%s，以最新 opus=%s 为基线", target.mid, baseline)
            return

        fresh_ids = sorted({
            e["opus_id"] for e in entries if state.is_newer_opus(e["opus_id"])
        })[:MAX_PER_ROUND]
        if not fresh_ids:
            return

        # bilibili_watch 门禁过滤目标群；过滤后为空不推进 state（与无群同语义）
        group_openids = self.tiers.filter_groups("bilibili_watch", self._target_groups())
        if not group_openids:
            _logger.warning("B 站图文动态无可达目标群（未学习或被门禁过滤），本轮暂存不发")
            return

        if target.mode == "full":
            for opus_id in fresh_ids:
                dyn = await self._load_opus_detail(client, opus_id)
                if dyn is not None:
                    await self._send_opus_full(client, target, dyn, group_openids)
                # 逐条推进并落盘（详情失败也推进，避免单条坏数据卡死队列）
                state.mark_opus(opus_id)
        else:
            entries_by_id = {e["opus_id"]: e for e in entries}
            name = await self._resolve_name(client, target, state)
            for opus_id in fresh_ids:
                entry = entries_by_id.get(opus_id) or {}
                title = str(entry.get("title") or "").strip() or "新动态"
                text = format_notice_text(name, title, opus_link(opus_id))
                for group_openid in group_openids:
                    await self.sender.send_text(group_openid, text)
                state.mark_opus(opus_id)
                _logger.info(
                    "已推送 B 站图文通知 mid=%s opus=%s groups=%d", target.mid, opus_id, len(group_openids)
                )

    async def _load_opus_detail(self, client, opus_id) -> Optional[dict]:
        try:
            item = await client.fetch_opus_detail(opus_id)
        except BilibiliApiError as error:
            _logger.warning("图文详情拉取失败，跳过该条 opus=%s: %s", opus_id, error)
            return None
        dyn = parse_opus_detail(item, opus_id=opus_id)
        if dyn is None:
            _logger.warning("图文详情解析失败，跳过该条 opus=%s", opus_id)
        return dyn

    async def _send_opus_full(
        self, client, target: BilibiliTarget, dyn: dict, group_openids: List[str]
    ) -> None:
        text = format_opus_text(dyn)
        if text:
            # 先发文字再逐张发图
            for group_openid in group_openids:
                await self.sender.send_text(group_openid, text)
        else:
            _logger.info("图文动态 %s 无文字内容，只发图片", dyn["id_str"])

        for index, pic_url in enumerate(dyn["pics"]):
            try:
                path = await client.download_image(
                    pic_url, self.tmp_dir, name=f"opus_{dyn['id_str']}_{index}", mid=target.mid
                )
            except BilibiliApiError as error:
                _logger.warning("下载动态图片失败，跳过该图 url=%s error=%s", pic_url, error)
                continue
            try:
                await self.sender.send_image(path, "", group_openids)
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
        _logger.info(
            "已推送 B 站图文动态 mid=%s id=%s pics=%d groups=%d link=%s",
            target.mid, dyn["id_str"], len(dyn["pics"]), len(group_openids), dyn["link"],
        )

    # ---------- 视频路（两模式统一通知格式） ----------

    async def _tick_videos(self, client, target: BilibiliTarget, state: TargetState) -> None:
        try:
            items = await client.fetch_videos(target.mid)
        except BilibiliApiError as error:
            _logger.warning("B 站视频列表请求失败 mid=%s，跳过本轮视频路: %s", target.mid, error)
            return
        videos = [v for v in (parse_video_item(i) for i in items) if v is not None]
        if not videos:
            return

        if not state.has_video_baseline:
            newest = max(videos, key=lambda v: (v["created"], v["bvid"]))
            state.mark_video(newest["created"], newest["bvid"])
            _logger.info("视频路首次轮询 mid=%s，以最新 bvid=%s 为基线", target.mid, newest["bvid"])
            return

        fresh = [v for v in videos if state.is_newer_video(v["created"], v["bvid"])]
        if not fresh:
            return
        fresh.sort(key=lambda v: (v["created"], v["bvid"]))
        fresh = fresh[:MAX_PER_ROUND]

        # bilibili_watch 门禁过滤目标群；过滤后为空不推进 state（与无群同语义）
        group_openids = self.tiers.filter_groups("bilibili_watch", self._target_groups())
        if not group_openids:
            _logger.warning("B 站视频动态无可达目标群（未学习或被门禁过滤），本轮暂存不发")
            return

        name = await self._resolve_name(client, target, state, videos=items)
        for video in fresh:
            title = video["title"] or "新视频"
            text = format_notice_text(name, title, video_link(video["bvid"]))
            for group_openid in group_openids:
                await self.sender.send_text(group_openid, text)
            # 逐条推进并落盘，进程中途崩溃也不会重复推送
            state.mark_video(video["created"], video["bvid"])
            _logger.info(
                "已推送 B 站视频通知 mid=%s bvid=%s title=%r groups=%d",
                target.mid, video["bvid"], video["title"], len(group_openids),
            )

    def _target_groups(self) -> List[str]:
        manual = self.config.target.group_openids
        if manual:
            return sorted(set(manual))
        return sorted(self.group_store.openids())
