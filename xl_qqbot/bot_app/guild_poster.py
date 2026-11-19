"""Render the guild recruitment roster as a single-column PNG."""

from bisect import bisect_left
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Protocol, TypeVar

from PIL import Image, ImageDraw, ImageFont


class GuildPosterRow(Protocol):
    @property
    def guild_uid(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def member_count(self) -> int | None: ...

    @property
    def capacity(self) -> int | None: ...

    @property
    def last_rank(self) -> int | None: ...

    @property
    def captured_at(self) -> float | None: ...

    @property
    def vacancies(self) -> int | None: ...


_Row = TypeVar("_Row", bound=GuildPosterRow)
_FONT_DIR = Path(__file__).resolve().parents[2] / "xl_updata_server" / "assets" / "fonts"
_BEIJING = timezone(timedelta(hours=8))
_TIERS = {
    "一档公会": ("上期第 1–5 名", "#dceae9"),
    "二档公会": ("上期第 6–50 名", "#dfebf1"),
    "三档公会": ("上期第 51 名及以后", "#e6edf1"),
    "暂无排名": ("", "#e6edf1"),
}
_WIDTH = 1080
_LEFT, _RIGHT = 54, 1026
_NAME, _RANK, _MEMBERS, _VACANCIES = 80, 557, 737, 930
_TOP, _HEADER_HEIGHT, _ROW_HEIGHT, _GAP = 273, 121, 72, 25
_INK, _SECONDARY = "#1d3547", "#4a6170"
_GREEN, _AMBER = "#1d604c", "#a97033"


def grouped_rows(rows: Iterable[_Row]) -> tuple[tuple[str, tuple[_Row, ...]], ...]:
    """Return the occupied tiers, ordered by rank, vacancies, name and UID."""
    buckets: list[list[_Row]] = [[] for _ in _TIERS]
    for row in rows:
        index = 3 if row.last_rank is None else bisect_left((5, 50), row.last_rank)
        buckets[index].append(row)

    def sort_key(row: GuildPosterRow) -> tuple[int, bool, int, str, str]:
        vacancies = row.vacancies
        return row.last_rank or 0, vacancies is None, -(vacancies or 0), row.name, row.guild_uid

    return tuple(
        (label, tuple(sorted(bucket, key=sort_key)))
        for label, bucket in zip(_TIERS, buckets)
        if bucket
    )


def _capture_label(rows: Iterable[GuildPosterRow]) -> str:
    timestamps = [row.captured_at for row in rows if row.captured_at is not None]
    if not timestamps:
        return "暂无采集记录"

    earliest = datetime.fromtimestamp(min(timestamps), _BEIJING)
    latest = datetime.fromtimestamp(max(timestamps), _BEIJING)
    include_year = earliest.year != latest.year

    def date_and_time(when: datetime) -> str:
        year = f"{when.year} 年 " if include_year else ""
        return f"{year}{when.month} 月 {when.day} 日 {when:%H:%M}"

    first = date_and_time(earliest)
    last = date_and_time(latest)
    if first == last:
        span = first
    elif earliest.date() == latest.date():
        span = f"{first}–{latest:%H:%M}"
    else:
        span = f"{first}–{last}"
    return f"采集时间  {span} （北京时间）"


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    weight = "Bold" if bold else "Regular"
    return ImageFont.truetype(str(_FONT_DIR / f"NotoSansCJKsc-{weight}.otf"), size)


def _fit_name(
    name: str, width: int, font: ImageFont.FreeTypeFont,
) -> tuple[ImageFont.FreeTypeFont, tuple[str, ...]]:
    name = " ".join(name.splitlines()).expandtabs(1).strip() or "—"
    right_edge = font.getbbox(name)[2]
    if right_edge <= width:
        return font, (name,)

    minimum = 25
    size = max(minimum, int(font.size * width / right_edge))
    smaller = font.font_variant(size=size)
    while size > minimum and smaller.getbbox(name)[2] > width:
        size -= 1
        smaller = font.font_variant(size=size)
    if smaller.getbbox(name)[2] <= width:
        return smaller, (name,)

    lines = []
    line = ""
    for character in name:
        next_line = line + character
        if line and smaller.getbbox(next_line)[2] > width:
            lines.append(line.rstrip())
            line = character.lstrip()
        else:
            line = next_line
    if line:
        lines.append(line.rstrip())
    return smaller, tuple(lines)


def render_guild_poster(rows: Iterable[GuildPosterRow], stale_ids: Iterable[str] = ()) -> bytes:
    """Render all roster rows, marking those whose latest fetch failed."""
    roster = tuple(rows)
    stale = set(stale_ids).intersection(row.guild_uid for row in roster)
    name_font = _font(43, bold=True)
    groups = []
    for label, tier in grouped_rows(roster):
        entries = []
        for row in tier:
            width = 366 if row.guild_uid in stale else 385
            font, lines = _fit_name(row.name, width, name_font)
            height = _ROW_HEIGHT + (len(lines) - 1) * (font.size + 7)
            entries.append((row, font, lines, height))
        groups.append((label, entries, _HEADER_HEIGHT + sum(entry[3] for entry in entries)))

    body_height = sum(height + _GAP for _, _, height in groups) if groups else 145
    footer_y = _TOP + body_height
    image = Image.new("RGB", (_WIDTH, footer_y + 85 + (43 if stale else 0)), "#edf2f5")
    draw = ImageDraw.Draw(image)
    regular24 = _font(24)
    regular25 = _font(25)
    regular26 = _font(26)
    regular32 = _font(32)
    bold28 = _font(28, bold=True)
    bold31 = _font(31, bold=True)
    bold36 = _font(36, bold=True)
    bold37 = _font(37, bold=True)

    draw.rectangle((0, 0, _WIDTH - 1, 13), fill="#23617a")
    draw.text((_LEFT, 53), "★", font=bold31, fill=_AMBER, anchor="lt")
    draw.text((103, 51), "星落 / 公会招募", font=_font(32, bold=True), fill=_SECONDARY, anchor="lt")
    draw.text((_LEFT, 108), "公会一览", font=_font(93, bold=True), fill=_INK, anchor="lt")
    draw.text((_RIGHT, 111), f"{len(roster)} 家公会", font=_font(42, bold=True), fill=_INK, anchor="rt")
    with_vacancies = sum((row.vacancies or 0) > 0 for row in roster)
    draw.text((_RIGHT, 171), f"{with_vacancies} 家有空位", font=bold31, fill=_GREEN, anchor="rt")
    draw.text((_LEFT, 211), _capture_label(roster), font=_font(27), fill=_SECONDARY, anchor="lt")
    draw.line((_LEFT, 250, _RIGHT, 250), fill="#cfdae2", width=2)

    top = _TOP
    if not groups:
        draw.rounded_rectangle((_LEFT, top, _RIGHT, top + 119), radius=24, fill="white")
        draw.text((_WIDTH // 2, top + 60), "暂无公会", font=bold31, fill=_SECONDARY, anchor="mm")

    for label, entries, group_height in groups:
        description, color = _TIERS[label]
        draw.rounded_rectangle((_LEFT, top, _RIGHT, top + group_height - 1), radius=24, fill="white")
        draw.rounded_rectangle((_LEFT, top, _RIGHT, top + 67), radius=24, fill=color)
        draw.rectangle((_LEFT, top + 24, _RIGHT, top + 67), fill=color)
        draw.text((_NAME, top + 17), label, font=bold37, fill=_INK, anchor="lt")
        count = f"{len(entries)} 家"
        subtitle = f"{description}  ・  {count}" if description else count
        draw.text((1000, top + 23), subtitle, font=regular26, fill=_SECONDARY, anchor="rt")

        draw.text((_NAME, top + 83), "公会名称", font=regular25, fill=_SECONDARY, anchor="lt")
        for x, column in ((_RANK, "上期排名"), (_MEMBERS, "当前人数"), (_VACANCIES, "空位")):
            draw.text((x, top + 83), column, font=regular25, fill=_SECONDARY, anchor="mt")

        row_top = top + 123
        for index, (row, font, lines, height) in enumerate(entries):
            center = row_top + (height - 5) // 2
            if index % 2 == 0:
                draw.rounded_rectangle((64, row_top, 1016, row_top + height - 6), radius=14, fill="#f3f7f9")
            line_height = font.size + 7
            y = center - (len(lines) - 1) * line_height // 2
            for line in lines:
                draw.text((_NAME, y), line, font=font, fill=_INK, anchor="lm")
                y += line_height
            if row.guild_uid in stale:
                draw.ellipse((455, center - 6, 467, center + 6), fill=_AMBER)

            rank = "—" if row.last_rank is None else f"第 {row.last_rank} 名"
            count = "—" if row.member_count is None else str(row.member_count)
            capacity = "—" if row.capacity is None else str(row.capacity)
            members = "—" if row.member_count is None and row.capacity is None else f"{count} / {capacity}"
            draw.text((_RANK, center), rank, font=regular32, fill=_INK, anchor="mm")
            draw.text((_MEMBERS, center), members, font=bold36, fill=_INK, anchor="mm")

            vacancies = row.vacancies
            if vacancies is None:
                badge, badge_fill, badge_ink = "待确认", "#e9eef2", "#526674"
            elif vacancies > 0:
                badge, badge_fill, badge_ink = f"余 {vacancies} 位", "#dfefe8", _GREEN
            else:
                badge, badge_fill, badge_ink = "已满", "#e9eef2", "#526674"
            draw.rounded_rectangle((860, center - 24, 1000, center + 24), radius=16, fill=badge_fill)
            draw.text((_VACANCIES, center), badge, font=bold28, fill=badge_ink, anchor="mm")
            row_top += height

        top += group_height + _GAP

    draw.line((_LEFT, footer_y + 2, _RIGHT, footer_y + 2), fill="#cdd9e1", width=2)
    draw.text((_LEFT, footer_y + 30), "有意入会，欢迎咨询", font=_font(31), fill=_SECONDARY, anchor="lt")
    if stale:
        draw.ellipse((_LEFT, footer_y + 77, _LEFT + 12, footer_y + 89), fill=_AMBER)
        draw.text(
            (_LEFT + 22, footer_y + 83), "圆点标记本次采集失败，已有数据沿用上次记录",
            font=regular24, fill=_SECONDARY, anchor="lm",
        )

    with BytesIO() as output:
        image.save(output, format="PNG")
        return output.getvalue()
