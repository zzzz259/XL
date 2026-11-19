from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from bot_app.guild_poster import _capture_label, grouped_rows, render_guild_poster
from PIL import Image, ImageChops


@dataclass(frozen=True)
class GuildRow:
    guild_uid: str
    name: str
    last_rank: int | None
    member_count: int | None = 28
    capacity: int | None = 30
    captured_at: float | None = None

    @property
    def vacancies(self) -> int | None:
        if self.member_count is None or self.capacity is None:
            return None
        return self.capacity - self.member_count


def test_grouped_rows_orders_rank_then_vacancies_name_and_uid():
    rows = [
        GuildRow("unranked", "未排名", None),
        GuildRow("51", "五十一名", 51),
        GuildRow("50", "五十名", 50),
        GuildRow("6-unknown", "未知人数", 6, None),
        GuildRow("6-1", "少空位", 6, 29),
        GuildRow("6-5-b", "乙书社", 6, 25),
        GuildRow("6-5-z", "星光社", 6, 25),
        GuildRow("6-5-a", "星光社", 6, 25),
        GuildRow("5", "第五名", 5),
        GuildRow("1", "第一名", 1, 30),
    ]

    assert [(label, [row.guild_uid for row in group]) for label, group in grouped_rows(rows)] == [
        ("一档公会", ["1", "5"]),
        ("二档公会", ["6-5-b", "6-5-a", "6-5-z", "6-1", "6-unknown", "50"]),
        ("三档公会", ["51"]),
        ("暂无排名", ["unranked"]),
    ]


def test_grouped_rows_omits_empty_groups():
    assert [label for label, _ in grouped_rows([GuildRow("50", "公会甲", 50)])] == ["二档公会"]
    assert grouped_rows([]) == ()


def test_capture_label_reports_beijing_times_across_midnight():
    def observed(uid: str, utc_time: str) -> GuildRow:
        return GuildRow(uid, "公会甲", 6, captured_at=datetime.fromisoformat(utc_time).timestamp())

    assert _capture_label([
        observed("later", "2026-11-19T16:04:00+00:00"),
        GuildRow("missing", "公会乙", 7),
        observed("earlier", "2026-11-19T15:58:00+00:00"),
    ]) == "采集时间  11 月 19 日 23:58–11 月 20 日 00:04 （北京时间）"
    assert _capture_label([
        observed("later", "2026-11-19T03:24:00+00:00"),
        observed("earlier", "2026-11-19T03:17:00+00:00"),
    ]) == "采集时间  11 月 19 日 11:17–11:24 （北京时间）"


def test_poster_renders_an_empty_roster_and_one_row_as_png():
    with Image.open(BytesIO(render_guild_poster([]))) as empty:
        assert empty.format == "PNG"
        assert empty.width == 1080
        empty.load()
        with Image.open(BytesIO(render_guild_poster([GuildRow("1", "公会甲", 1)]))) as roster:
            assert roster.format == "PNG"
            assert roster.width == empty.width
            assert roster.height > empty.height
            roster.load()


def test_poster_marks_only_listed_stale_rows_and_adds_a_legend():
    rows = [GuildRow("1", "公会甲", 1), GuildRow("2", "公会乙", 2)]
    fresh = render_guild_poster(rows)
    assert render_guild_poster(rows, stale_ids={"removed"}) == fresh

    with (
        Image.open(BytesIO(fresh)) as reference,
        Image.open(BytesIO(render_guild_poster(rows, stale_ids={"2"}))) as stale,
    ):
        assert stale.height > reference.height
        first_row = (64, 396, 1017, 463)
        second_row = (64, 468, 1017, 535)
        assert ImageChops.difference(reference.crop(first_row), stale.crop(first_row)).getbbox() is None
        assert ImageChops.difference(reference.crop(second_row), stale.crop(second_row)).getbbox() is not None


def test_long_name_wraps_without_overlapping_other_columns():
    short = GuildRow("1", "公会甲", 1)
    long = GuildRow("1", "超长公会名称" * 12, 1)
    with (
        Image.open(BytesIO(render_guild_poster([short]))) as compact,
        Image.open(BytesIO(render_guild_poster([long]))) as wrapped,
    ):
        assert wrapped.height > compact.height
        row_extra = wrapped.height - compact.height
        row_top = 396
        # The numeric columns stay centered on the expanded row.
        offset = row_extra // 2
        numeric = (470, row_top + 10, 1001, row_top + 56)
        shifted_numeric = (numeric[0], numeric[1] + offset, numeric[2], numeric[3] + offset)
        assert ImageChops.difference(compact.crop(numeric), wrapped.crop(shifted_numeric)).getbbox() is None
