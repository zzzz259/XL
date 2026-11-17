"""Guild war phase -> stage mapping resource and share-HP damage normalization."""
import json
import unittest
from pathlib import Path

try:
    from gacha_core import guild_stages
except ImportError:
    guild_stages = None


def resource(**changes):
    base = {
        "format": "guild-war-stages/1",
        "phases": {
            "1": {
                "config_id": 51400001,
                "stage_count": 2,
                "stages": [
                    {"stage_id": 51501011, "chapter_id": 50020101, "round": 1,
                     "boss_index": 1, "boss_id": 52010101, "share_hp_count": None},
                    {"stage_id": 51501012, "chapter_id": 50020101, "round": 1,
                     "boss_index": 2, "boss_id": 52010201, "share_hp_count": 3},
                ],
            },
            "2": {
                "config_id": 51400002,
                "stage_count": 1,
                "stages": [
                    {"stage_id": 51502011, "chapter_id": 50020201, "round": 1,
                     "boss_index": 1, "boss_id": 52020101, "share_hp_count": 2},
                ],
            },
        },
    }
    base.update(changes)
    return base


class StageMapTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(guild_stages, 'guild stage map is not implemented')

    def test_stage_ids_follow_chapter_round_order_within_a_phase(self):
        stage_map = guild_stages.GuildWarStageMap(resource())
        self.assertEqual(stage_map.stage_ids(1), (51501011, 51501012))
        self.assertEqual(stage_map.stage_ids(2), (51502011,))
        self.assertEqual(stage_map.phases(), (1, 2))

    def test_unknown_phase_returns_none_instead_of_guessing(self):
        stage_map = guild_stages.GuildWarStageMap(resource())
        self.assertIsNone(stage_map.stage_ids(99))
        self.assertIsNone(stage_map.stage(51509999))
        self.assertIsNone(stage_map.share_hp_count(51509999))
        self.assertIsNone(stage_map.expected_stage_count(99))

    def test_stage_details_carry_chapter_round_boss_and_share_hp(self):
        stage_map = guild_stages.GuildWarStageMap(resource())
        stage = stage_map.stage(51501012)
        self.assertEqual(stage['chapter_id'], 50020101)
        self.assertEqual(stage['round'], 1)
        self.assertEqual(stage['boss_index'], 2)
        self.assertEqual(stage_map.share_hp_count(51501012), 3)
        self.assertIsNone(stage_map.share_hp_count(51501011))

    def test_display_damage_rounds_up_only_for_non_v1_shared_hp(self):
        self.assertEqual(guild_stages.display_damage_hp(100, 2, 3), 34)
        self.assertEqual(guild_stages.display_damage_hp(99, 2, 3), 33)
        # version==1 keeps the raw value even with shared HP
        self.assertEqual(guild_stages.display_damage_hp(100, 1, 3), 100)
        # unknown share HP keeps the raw value
        self.assertEqual(guild_stages.display_damage_hp(100, 2, None), 100)
        self.assertEqual(guild_stages.display_damage_hp(100, 2, 0), 100)
        with self.assertRaises(ValueError):
            guild_stages.display_damage_hp('100', 2, 3)

    def test_display_damage_uses_exact_integer_arithmetic_beyond_float_precision(self):
        raw = 2**53 + 1  # not representable as an IEEE754 double
        self.assertEqual(guild_stages.display_damage_hp(raw, 2, 2), 2**52 + 1)
        # math.ceil(raw / 2) rounds raw down to 2**53 first and would return 2**52
        self.assertEqual(guild_stages.display_damage_hp(raw, 2, 3), (raw + 2) // 3)
        self.assertEqual(guild_stages.display_damage_hp(raw, 1, 2), raw)

    def test_invalid_resources_are_rejected(self):
        bad_stage_count = resource()
        bad_stage_count['phases']['1']['stage_count'] = 3
        with self.assertRaises(ValueError):
            guild_stages.GuildWarStageMap(bad_stage_count)

        duplicate_within_phase = resource()
        stages = duplicate_within_phase['phases']['1']['stages']
        stages.append(dict(stages[0]))
        with self.assertRaises(ValueError):
            guild_stages.GuildWarStageMap(duplicate_within_phase)

        unknown_keys = resource()
        unknown_keys['phases']['1']['stages'][0]['stageId'] = 51501011
        with self.assertRaises(ValueError):
            guild_stages.GuildWarStageMap(unknown_keys)

        not_a_mapping = resource()
        not_a_mapping['phases'] = []
        with self.assertRaises(ValueError):
            guild_stages.GuildWarStageMap(not_a_mapping)

        bad_share = resource()
        bad_share['phases']['1']['stages'][0]['share_hp_count'] = 0
        with self.assertRaises(ValueError):
            guild_stages.GuildWarStageMap(bad_share)

    def test_packaged_resource_loads_and_validates_against_protocol(self):
        stage_map = guild_stages.load_stage_map()
        self.assertGreaterEqual(len(stage_map.phases()), 29)
        for phase in stage_map.phases():
            stages = stage_map.stage_ids(phase)
            self.assertEqual(len(stages), stage_map.expected_stage_count(phase))
            self.assertEqual(len(set(stages)), len(stages))
        # phases 5+ reuse the 31-chapter layout; early phases are smaller
        self.assertEqual(stage_map.expected_stage_count(1), 30)
        self.assertEqual(stage_map.expected_stage_count(5), 155)
        # consecutive phases may share stage ids by design (alternate layouts)
        self.assertEqual(stage_map.stage_ids(5), stage_map.stage_ids(6))
        raw = json.loads(Path(guild_stages.default_resource_path()).read_text(encoding='utf-8'))
        self.assertEqual(raw['format'], 'guild-war-stages/1')


if __name__ == '__main__':
    unittest.main()
