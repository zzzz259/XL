"""角色名多策略模糊匹配：别名表 + 拼音索引 + 评分排序（纯逻辑，可单测）。

评分优先级（从高到低，命中取最高分）：
- 100 官方中文名完全一致 / 别名完全一致 / 角色 ID 完全一致
- 98  英文名完全一致（大小写不敏感）
- 96  拼音全拼完全一致（feilisi → 菲尼斯）
- 94  拼音首字母完全一致（fns → 菲尼斯）
- 90+ 中文编辑距离高相似（difflib ratio ≥ 0.8，得分 90+5*ratio）
- 85+ 拼音高相似（ratio ≥ 0.85，得分 85+10*ratio；同音字 菲尼丝/菲妮斯 ≈ 菲尼斯）
- 80+ 名称部分包含（得分 80+4*短/长，恒 < 85，只会进候选）

判定：top1 ≥ 95 直回；85~95 且领先 top2 ≥ 5 带"已为你匹配到"直回；
70~85 或分差不足回候选（最多 3 个）；top1 < 70 未找到。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

try:
    from pypinyin import Style, lazy_pinyin
    _HAS_PYPINYIN = True
except ImportError:  # 服务器装不上时降级：拼音策略不可用，其余策略照常
    _HAS_PYPINYIN = False

_logger = logging.getLogger(__name__)

# 命中途径（日志/测试用）
VIA_CN = "中文名"
VIA_ALIAS = "别名"
VIA_ID = "ID"
VIA_EN = "英文名"
VIA_PINYIN = "拼音全拼"
VIA_INITIAL = "拼音首字母"
VIA_FUZZY = "模糊相似"
VIA_PINYIN_SIM = "拼音相似"
VIA_CONTAIN = "名称包含"

SCORE_EN = 98.0
SCORE_PINYIN = 96.0
SCORE_INITIAL = 94.0
FUZZY_THRESHOLD = 0.8
PINYIN_SIM_THRESHOLD = 0.85
CONTAIN_MIN_QUERY_LEN = 2  # 单字子串匹配太噪，要求至少 2 字

_CIRCLED = ["①", "②", "③", "④", "⑤"]


@dataclass(frozen=True)
class Match:
    character_id: str
    name: str  # 官方中文名
    star: int
    score: float
    via: str


@dataclass(frozen=True)
class MatchOutcome:
    kind: str  # "direct" | "guess" | "candidates" | "not_found"
    match: Match | None = None
    candidates: tuple = ()


def render_candidates_message(options: list) -> str:
    """候选列表消息（含星级与"都不是"项），options 最多 3 个。"""
    lines = ["没有完全一致的角色，你是不是想找："]
    for index, option in enumerate(options, 1):
        star = f" ★{option.star}" if option.star else ""
        lines.append(f"{_CIRCLED[index - 1]} {option.name}{star}")
    lines.append(f"{_CIRCLED[len(options)]} 都不是")
    numbers = "/".join(str(i) for i in range(1, len(options) + 2))
    lines.append(f"回复数字 {numbers} 选择（仅本次提问有效，120秒内）")
    return "\n".join(lines)


class CharacterMatcher:
    """角色匹配器：角色数据 + 人工别名(aliases.json) + 纠错学习(learned_aliases.json)。"""

    def __init__(self, character_data, data_dir):
        self._character_data = Path(character_data)
        self._data_dir = Path(data_dir)
        self._aliases_path = self._data_dir / "aliases.json"
        self._learned_path = self._data_dir / "learned_aliases.json"
        self._mtimes: dict = {}
        self._characters: dict[str, dict] = {}    # id -> {cn, en, star}
        self._alias_map: dict[str, set] = {}      # id -> 小写别名集合
        self._learned: dict = {}                  # 查询词 -> {char_id: 次数, "users": [...]}
        self._pinyin_cache: dict[str, tuple] = {}
        if not _HAS_PYPINYIN:
            _logger.warning("未安装 pypinyin，拼音相关匹配策略不可用（其余策略照常）")
        self._reload(force=True)

    # ---------- 数据加载 ----------

    def _file_mtime(self, path: Path) -> float | None:
        try:
            return path.stat().st_mtime
        except OSError:
            return None

    def _reload(self, force: bool = False) -> None:
        paths = (self._character_data, self._aliases_path, self._learned_path)
        mtimes = {str(p): self._file_mtime(p) for p in paths}
        if not force and mtimes == self._mtimes:
            return
        self._mtimes = mtimes

        characters: dict[str, dict] = {}
        try:
            payload = json.loads(self._character_data.read_text(encoding="utf-8"))
            raw_characters = payload.get("characters", {})
        except (OSError, ValueError):
            raw_characters = {}
        for character_id, data in (raw_characters.items() if isinstance(raw_characters, dict) else []):
            if not isinstance(data, dict):
                continue
            cn_name, _, en_name = str(data.get("name", "")).partition("/")
            cn_name = cn_name.strip()
            if not cn_name:
                continue
            characters[str(character_id)] = {
                "cn": cn_name,
                "en": en_name.strip(),
                "star": self._safe_star(data.get("star")),
            }

        manual: dict = {}
        try:
            loaded = json.loads(self._aliases_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manual = loaded
        except (OSError, ValueError):
            pass

        learned: dict = {}
        try:
            loaded = json.loads(self._learned_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                learned = loaded
        except (OSError, ValueError):
            pass

        alias_map: dict[str, set] = {}
        for character_id, info in characters.items():
            aliases = {info["cn"].lower()}
            if info["en"]:
                aliases.add(info["en"].lower())
            extra = manual.get(info["cn"])
            if isinstance(extra, list):
                aliases.update(str(a).strip().lower() for a in extra if str(a).strip())
            alias_map[character_id] = aliases

        # 学习升级：同一查询词被 ≥2 个不同用户选定到同一角色 → 升级为别名（内存表，不动 aliases.json）
        for word, entry in learned.items():
            character_id = self._qualified_learn_target(entry)
            if character_id and character_id in alias_map and word.strip():
                alias_map[character_id].add(word.strip().lower())

        self._characters = characters
        self._alias_map = alias_map
        self._learned = learned
        self._pinyin_cache = {}

    @staticmethod
    def _safe_star(value) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _qualified_learn_target(entry) -> str | None:
        """学习升级判定：≥2 个不同用户，且领先角色被选中 ≥2 次。"""
        if not isinstance(entry, dict):
            return None
        users = entry.get("users")
        if not isinstance(users, list) or len({str(u) for u in users}) < 2:
            return None
        counts = {k: v for k, v in entry.items() if k != "users"}
        if not counts:
            return None
        best = max(counts, key=lambda k: _safe_count(counts[k]))
        if _safe_count(counts[best]) < 2:
            return None
        return str(best)

    # ---------- 拼音 ----------

    def _pinyin_of(self, text: str) -> tuple[str, str]:
        """(全拼拼接, 首字母拼接)；无 pypinyin 时返回 ("", "")。"""
        if not _HAS_PYPINYIN:
            return "", ""
        cached = self._pinyin_cache.get(text)
        if cached is not None:
            return cached
        full = "".join(lazy_pinyin(text)).lower()
        initials = "".join(lazy_pinyin(text, style=Style.FIRST_LETTER)).lower()
        result = (full, initials)
        self._pinyin_cache[text] = result
        return result

    # ---------- 主流程 ----------

    @staticmethod
    def clean(query: str) -> str:
        return (query or "").strip().strip("\"'“”‘’").strip()

    def match(self, query: str) -> MatchOutcome:
        query = self.clean(query)
        if not query:
            return MatchOutcome(kind="not_found")
        self._reload()

        scored: list[Match] = []
        lowered = query.lower()
        query_full_py, query_initials = self._pinyin_of(query)

        for character_id, info in self._characters.items():
            score, via = self._score_character(query, lowered, query_full_py, query_initials, character_id, info)
            if score >= 70:
                scored.append(Match(
                    character_id=character_id,
                    name=info["cn"],
                    star=info["star"],
                    score=round(score, 2),
                    via=via,
                ))

        if not scored:
            return MatchOutcome(kind="not_found")
        scored.sort(key=lambda m: (-m.score, -m.star, m.name))
        return self._decide(scored)

    def _score_character(self, query, lowered, query_full_py, query_initials, character_id, info) -> tuple[float, str]:
        cn = info["cn"]
        en = info["en"]
        aliases = self._alias_map.get(character_id, set())

        # 按优先级顺序评定，同分取高优先级（先命中者）
        if query == cn:
            return 100.0, VIA_CN
        if query == character_id:
            return 100.0, VIA_ID
        if en and lowered == en.lower():
            return SCORE_EN, VIA_EN
        if lowered in aliases:
            return 100.0, VIA_ALIAS

        char_full_py, char_initials = self._pinyin_of(cn)
        if query_full_py and query_full_py == char_full_py:
            return SCORE_PINYIN, VIA_PINYIN
        if query_initials and len(query) >= 2 and query_initials == char_initials:
            return SCORE_INITIAL, VIA_INITIAL

        best = 0.0, ""
        # 中文编辑距离：对中文名与全部别名取最高相似（短查询压线噪声大，要求 ≥3 字）
        fuzzy = max([_ratio(query, cn)] + [_ratio(query, a) for a in aliases])
        if len(query) >= 3 and fuzzy >= FUZZY_THRESHOLD:
            best = max(best, (90.0 + 5.0 * fuzzy, VIA_FUZZY))
        # 拼音高相似：同音异形字（菲尼丝/菲妮斯 ≈ 菲尼斯，拼音一致 ratio=1）
        if query_full_py and char_full_py:
            py_sim = _ratio(query_full_py, char_full_py)
            if py_sim >= PINYIN_SIM_THRESHOLD:
                best = max(best, (85.0 + 10.0 * py_sim, VIA_PINYIN_SIM))
        # 名称部分包含（双向），单字输入不触发
        if len(query) >= CONTAIN_MIN_QUERY_LEN and query != cn:
            contain_targets = [cn] + [a for a in aliases if _mostly_chinese(a)]
            for target in contain_targets:
                if not target or len(target) < len(query):
                    continue
                if query in target or target in query:
                    contain_score = 80.0 + 4.0 * min(len(query), len(target)) / max(len(query), len(target))
                    best = max(best, (contain_score, VIA_CONTAIN))
        if best[0] > 0:
            return best
        return 0.0, ""

    @staticmethod
    def _decide(scored: list) -> MatchOutcome:
        top = scored[0]
        second_score = scored[1].score if len(scored) > 1 else 0.0
        # 直回/带提示直回都要求明显领先（≥5）：并列第一（如同别名）必须回候选
        if top.score >= 95:
            if top.score - second_score >= 5:
                return MatchOutcome(kind="direct", match=top)
            return MatchOutcome(kind="candidates", candidates=tuple(scored[:3]))
        if top.score >= 85:
            if top.score - second_score >= 5:
                return MatchOutcome(kind="guess", match=top)
            return MatchOutcome(kind="candidates", candidates=tuple(scored[:3]))
        if top.score >= 70:
            return MatchOutcome(kind="candidates", candidates=tuple(scored[:3]))
        return MatchOutcome(kind="not_found")

    # ---------- 纠错学习 ----------

    def is_alias_of(self, query: str, character_id: str) -> bool:
        query = self.clean(query).lower()
        return bool(query) and query in self._alias_map.get(character_id, set())

    def record_selection(self, query: str, character_id: str, member_openid: str) -> dict | None:
        """记录"用户从候选选定角色"；返回记录摘要供日志，不该记录时返回 None。"""
        query = self.clean(query)
        if not query or not member_openid:
            return None
        self._reload()
        info = self._characters.get(character_id)
        if info is None:
            return None
        # 已是该角色别名（官方名/人工别名/已学别名）→ 不重复记录
        if self.is_alias_of(query, character_id):
            return None

        entry = self._learned.setdefault(query, {})
        users = entry.setdefault("users", [])
        if not isinstance(users, list):
            users = []
            entry["users"] = users
        count = _safe_count(entry.get(character_id)) + 1
        entry[character_id] = count
        if member_openid not in users:
            users.append(member_openid)
        self._save_learned()

        upgraded = self._qualified_learn_target(entry)
        if upgraded == character_id:
            self._alias_map.setdefault(character_id, set()).add(query.lower())
        return {
            "query": query,
            "character_id": character_id,
            "name": info["cn"],
            "count": count,
            "users": len({str(u) for u in users}),
            "upgraded": upgraded == character_id,
        }

    def _save_learned(self) -> None:
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            temporary = self._learned_path.with_suffix(".part")
            temporary.write_text(
                json.dumps(self._learned, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(self._learned_path)
        except OSError:
            _logger.exception("写入 learned_aliases.json 失败")
        self._mtimes[str(self._learned_path)] = self._file_mtime(self._learned_path)


def _safe_count(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _mostly_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)
