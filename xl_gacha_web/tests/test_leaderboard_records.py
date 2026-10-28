"""Offline decoders for archived business PB payloads: 2003 lineups, 2306 recommendations."""
import unittest

try:
    from gacha_core import leaderboard_records as records
except ImportError:
    records = None
from gacha_core.gacha import GachaError
from gacha_core.gacha import blob as b
from gacha_core.gacha import integer as i


def lineup_entry(card_id, skin_id):
    return i(1, card_id) + i(2, skin_id)


def lineup_round(score, *cards):
    return i(1, score) + b''.join(b(2, card) for card in cards)


def lineup_response(uin, *rounds):
    return i(1, uin) + b''.join(b(2, round_) for round_ in rounds)


def card_info(card_id, level=10, quality=4, grade=6, fashion_id=700, position=3):
    return (i(3, card_id) + i(4, level) + i(5, quality)
            + i(6, grade) + i(7, fashion_id) + i(8, position))


def skill_level(skill_id, level):
    return i(1, skill_id) + i(2, level)


def formation(stage_id, fighter_uin, name, damage, battle_uid,
              battle_time=65, version=2, cards=(), skills=()):
    return (i(1, stage_id) + i(2, fighter_uin) + b(3, name.encode())
            + b''.join(b(4, card) for card in cards)
            + i(6, damage) + i(7, battle_uid)
            + b''.join(b(8, skill) for skill in skills)
            + i(9, battle_time) + i(10, version))


def recommendation_response(*rows):
    return b''.join(b(1, row) for row in rows)


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, command, body=b''):
        self.calls.append((command, body))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class IllusionLineupDecoderTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(records, 'leaderboard record decoders are not implemented')

    def test_rounds_keep_int64_scores_and_sort_map_cards_by_card_id(self):
        body = lineup_response(
            9007199254740993,
            lineup_round(9223372036854775807,
                         lineup_entry(30, 5), lineup_entry(7, 0), lineup_entry(200, 12)),
            lineup_round(-5, lineup_entry(9, 1)),
        )
        rounds = records.decode_illusion_lineup(body, 9007199254740993)
        self.assertEqual(rounds, [
            {'round': 1, 'score': '9223372036854775807',
             'cards': [{'cardId': '7', 'skinId': '0'},
                       {'cardId': '30', 'skinId': '5'},
                       {'cardId': '200', 'skinId': '12'}]},
            {'round': 2, 'score': '-5', 'cards': [{'cardId': '9', 'skinId': '1'}]},
        ])

    def test_identity_mismatch_and_missing_rounds_are_distinguishable(self):
        with self.assertRaisesRegex(ValueError, 'LINEUP_IDENTITY_MISMATCH'):
            records.decode_illusion_lineup(lineup_response(1), '2')
        self.assertEqual(records.decode_illusion_lineup(lineup_response(9007199254740993),
                                                        9007199254740993), [])

    def test_challenge_and_card_bounds_match_research_script(self):
        too_many_rounds = lineup_response(1, *(lineup_round(0) for _ in range(17)))
        with self.assertRaisesRegex(ValueError, 'LINEUP_ROUND_LIMIT'):
            records.decode_illusion_lineup(too_many_rounds, 1)
        too_many_cards = lineup_response(1, lineup_round(0, *(lineup_entry(n, 0) for n in range(33))))
        with self.assertRaisesRegex(ValueError, 'LINEUP_CARD_LIMIT'):
            records.decode_illusion_lineup(too_many_cards, 1)

    def test_invalid_map_entries_are_rejected(self):
        for bad in (lineup_entry(0, 0), lineup_entry(5, -1)):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'INVALID_LINEUP_CARD'):
                records.decode_illusion_lineup(lineup_response(1, lineup_round(0, bad)), 1)
        duplicate = lineup_response(1, lineup_round(0, lineup_entry(5, 0), lineup_entry(5, 1)))
        with self.assertRaisesRegex(ValueError, 'INVALID_LINEUP_CARD'):
            records.decode_illusion_lineup(duplicate, 1)
        merged_entry = lineup_response(1, lineup_round(0, lineup_entry(5, 0) + i(1, 5) + i(2, 1)))
        with self.assertRaises(ValueError):
            records.decode_illusion_lineup(merged_entry, 1)

    def test_wire_identity_integers_must_be_positive(self):
        with self.assertRaisesRegex(ValueError, 'LINEUP_IDENTITY_MISMATCH'):
            records.decode_illusion_lineup(lineup_response(0), 1)
        with self.assertRaisesRegex(ValueError, 'LINEUP_IDENTITY_MISMATCH'):
            records.decode_illusion_lineup(lineup_response(-7), 1)
        with self.assertRaisesRegex(ValueError, 'REFERENCE_RESPONSE_IDENTITY_MISMATCH'):
            records.decode_guild_recommendations(
                recommendation_response(formation(0, 1, 'a', 1, 1)), 51506311)
        for bad_row in (formation(51506311, 0, 'a', 1, 1),
                        formation(51506311, -3, 'a', 1, 1),
                        formation(51506311, 1, 'a', 1, 0)):
            with self.subTest(bad_row=bad_row), self.assertRaisesRegex(ValueError, 'INVALID_REFERENCE_IDENTITY'):
                records.decode_guild_recommendations(recommendation_response(bad_row), 51506311)
        damage_row = recommendation_response(formation(51506311, 1, 'a', -50, 7))
        self.assertEqual(records.decode_guild_recommendations(damage_row, 51506311)[0]['damageHp'], '-50')

    def test_malformed_wire_data_fails_safely(self):
        for body in (b'\x80', b(2, b'x'), i(1, 1) + i(1, 2)):
            with self.subTest(body=body), self.assertRaises(ValueError):
                records.decode_illusion_lineup(body, 1)


class GuildRecommendationDecoderTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(records, 'leaderboard record decoders are not implemented')

    def test_formations_keep_card_simple_skill_level_damage_battle_uid_and_version(self):
        body = recommendation_response(
            formation(51506311, 9007199254740993, '战士', 9223372036854775807, 12345678901234567,
                      battle_time=65, version=2,
                      cards=[card_info(101, level=60, quality=5, grade=7, fashion_id=900, position=1)],
                      skills=[skill_level(4001, 3)]))
        rows = records.decode_guild_recommendations(body, 51506311)
        self.assertEqual(rows, [{
            'stageId': 51506311,
            'fighterUin': '9007199254740993',
            'fighterName': '战士',
            'cardInfos': [{'cardId': 101, 'level': 60, 'quality': 5, 'grade': 7,
                           'fashionId': 900, 'position': 1}],
            'damageHp': '9223372036854775807',
            'battleUid': '12345678901234567',
            'skillLevels': [{'skillId': 4001, 'level': 3}],
            'battleTime': 65,
            'version': 2,
        }])

    def test_stage_identity_and_top100_bound_are_enforced(self):
        other_stage = recommendation_response(formation(51506312, 1, 'a', 1, 1))
        with self.assertRaisesRegex(ValueError, 'REFERENCE_RESPONSE_IDENTITY_MISMATCH'):
            records.decode_guild_recommendations(other_stage, 51506311)
        too_many = recommendation_response(*(formation(51506311, n + 1, 'a', 1, 1)
                                             for n in range(101)))
        with self.assertRaisesRegex(ValueError, 'REFERENCE_RESPONSE_IDENTITY_MISMATCH'):
            records.decode_guild_recommendations(too_many, 51506311)
        exactly_100 = recommendation_response(*(formation(51506311, n + 1, 'a', 1, 1)
                                                for n in range(100)))
        self.assertEqual(len(records.decode_guild_recommendations(exactly_100, 51506311)), 100)

    def test_empty_response_is_a_valid_empty_pool(self):
        self.assertEqual(records.decode_guild_recommendations(b'', 51506311), [])


class RequestHelperTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(records, 'leaderboard record decoders are not implemented')

    def test_lineup_helper_sends_uin_and_returns_research_aligned_rounds(self):
        body = lineup_response(77, lineup_round(3, lineup_entry(9, 1), lineup_entry(2, 4)))
        session = FakeSession(body)
        seen = []
        rounds = records.request_illusion_lineup(session, '77',
                                                 on_response=lambda *args: seen.append(args))
        self.assertEqual(session.calls, [(2003, i(1, 77))])
        self.assertEqual(rounds[0]['cards'], [{'cardId': '2', 'skinId': '4'},
                                              {'cardId': '9', 'skinId': '1'}])
        self.assertEqual(seen, [(2003, body, rounds)])

    def test_recommendation_helper_archives_raw_pb_through_callback(self):
        body = recommendation_response(formation(51506311, 7, 'a', 50, 9, version=1))
        session = FakeSession(body)
        archived = []
        rows = records.request_guild_recommendations(
            session, 51506311, on_response=lambda command, raw, decoded: archived.append((command, raw, decoded)))
        self.assertEqual(session.calls, [(2306, i(1, 51506311))])
        self.assertEqual(rows[0]['damageHp'], '50')
        self.assertEqual(archived, [(2306, body, rows)])

    def test_helper_without_callback_still_returns_decoded_records(self):
        session = FakeSession(lineup_response(5, lineup_round(1, lineup_entry(3, 0))))
        self.assertEqual(len(records.request_illusion_lineup(session, 5)), 1)
        session = FakeSession(b'')
        self.assertEqual(records.request_guild_recommendations(session, 51506311), [])

    def test_transport_error_stops_after_single_attempt_without_retry(self):
        session = FakeSession(GachaError('RPC_REJECTED'))
        with self.assertRaises(GachaError):
            records.request_illusion_lineup(session, 5)
        self.assertEqual(len(session.calls), 1)

    def test_decode_error_stops_before_callback_and_without_retry(self):
        session = FakeSession(b(2, b'x'))
        seen = []
        with self.assertRaises(ValueError):
            records.request_illusion_lineup(session, 5, on_response=lambda *args: seen.append(args))
        self.assertEqual(seen, [])
        self.assertEqual(len(session.calls), 1)

    def test_request_identity_integers_must_be_positive_and_fail_before_transport(self):
        session = FakeSession(lineup_response(5, lineup_round(1, lineup_entry(3, 0))))
        for bad in (0, -1, '0', '-3', 'x'):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, 'INVALID_LINEUP_UIN'):
                    records.request_illusion_lineup(session, bad)
                with self.assertRaisesRegex(ValueError, 'INVALID_REFERENCE_STAGE'):
                    records.request_guild_recommendations(session, bad)
        self.assertEqual(session.calls, [])

    def test_callback_must_be_callable(self):
        session = FakeSession(lineup_response(5))
        with self.assertRaises(ValueError):
            records.request_illusion_lineup(session, 5, on_response='archive')

    def test_business_commands_are_session_whitelisted(self):
        from gacha_core.session import REQUEST_RESPONSES
        self.assertEqual(REQUEST_RESPONSES[2003], 102003)
        self.assertEqual(REQUEST_RESPONSES[2306], 102306)


if __name__ == '__main__':
    unittest.main()
