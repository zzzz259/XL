"""Offline contract tests for the public guild-name lookup."""

import unittest
from dataclasses import asdict

from gacha_core.gacha import GachaError, blob, integer
from gacha_core.guild_search import search_guilds
from gacha_core.session import REQUEST_RESPONSES


class SearchSession:
    def __init__(self, response=b''):
        self.response = response
        self.calls = []

    def request(self, command, body):
        self.calls.append((command, body))
        return self.response


def guild_row(*, uid=9007199254740993, name=b'Guild', level=10, members=None, rank=None):
    info = integer(1, uid) + blob(2, name) + integer(5, level)
    if members is not None:
        info += integer(11, members)
    if rank is not None:
        info += integer(26, rank)
    return info


class GuildSearchTests(unittest.TestCase):
    def test_name_search_is_an_allowlisted_read(self):
        self.assertEqual(REQUEST_RESPONSES.get(10032), 110032)

    def test_search_sends_offset_utf8_name_and_unset_filters(self):
        session = SearchSession()
        search_guilds(session, '  星落  ', offset=17)
        self.assertEqual(session.calls, [(10032, bytes.fromhex(
            '08 11 12 06 e6 98 9f e8 90 bd 18 00 20 00 28 00 38 00 40 00',
        ))])

    def test_decodes_only_public_fields_and_preserves_large_id(self):
        first = guild_row(name='星落'.encode(), members=29, rank=13)
        first += blob(6, b'ignored extra field') + integer(20, 12345678)
        second = guild_row(uid=2**63 - 1, name=b'Another guild', level=11)
        session = SearchSession(blob(1, first) + blob(1, second) + integer(2, 1))

        result = search_guilds(session, 'guild')

        self.assertTrue(result.has_more)
        self.assertIsInstance(result.guilds, tuple)
        self.assertEqual(tuple(asdict(guild) for guild in result.guilds), (
            {'guild_uid': '9007199254740993', 'name': '星落', 'level': 10,
             'member_count': 29, 'last_rank': 13},
            {'guild_uid': '9223372036854775807', 'name': 'Another guild', 'level': 11,
             'member_count': 0, 'last_rank': None},
        ))
        self.assertIsNone(result.guilds[1].capacity)

    def test_capacity_follows_verified_level_table(self):
        response = b''.join(blob(1, guild_row(uid=level, level=level)) for level in range(1, 11))
        result = search_guilds(SearchSession(response), 'guild')
        self.assertEqual(tuple(guild.capacity for guild in result.guilds),
                         (20, 21, 22, 23, 24, 25, 26, 27, 28, 30))

    def test_nonpositive_previous_rank_means_unranked(self):
        response = blob(1, guild_row(uid=1, rank=0)) + blob(1, guild_row(uid=2, rank=-1))
        result = search_guilds(SearchSession(response), 'guild')
        self.assertEqual(tuple(guild.last_rank for guild in result.guilds), (None, None))

    def test_empty_page_defaults_to_no_more(self):
        session = SearchSession()
        result = search_guilds(session, 'guild')
        self.assertEqual(result.guilds, ())
        self.assertFalse(result.has_more)
        self.assertTrue(session.calls[0][1].startswith(b'\x08\x00'))

    def test_invalid_name_never_calls_gateway(self):
        session = SearchSession()
        for name in ('', ' \t\n　', None, '\ud800'):
            with self.subTest(name=name):
                with self.assertRaises(GachaError) as error:
                    search_guilds(session, name)
                self.assertEqual(error.exception.code, 'INVALID_GUILD_SEARCH_NAME')
        self.assertEqual(session.calls, [])

    def test_invalid_offset_never_calls_gateway(self):
        session = SearchSession()
        for offset in (-1, 2**31, True, 1.5, '1'):
            with self.subTest(offset=offset):
                with self.assertRaises(GachaError) as error:
                    search_guilds(session, 'guild', offset=offset)
                self.assertEqual(error.exception.code, 'INVALID_GUILD_SEARCH_OFFSET')
        self.assertEqual(session.calls, [])

    def test_unusable_row_fails_page_with_fixed_code(self):
        for invalid in (guild_row(uid=-1), guild_row(uid=0), guild_row(name=b' '),
                        guild_row(level=0), guild_row(members=-1)):
            response = blob(1, guild_row()) + blob(1, invalid)
            with self.subTest(row=invalid):
                with self.assertRaises(GachaError) as error:
                    search_guilds(SearchSession(response), 'guild')
                self.assertEqual(error.exception.code, 'GUILD_SEARCH_PROTOCOL_ERROR')

    def test_malformed_wire_fails_with_fixed_code(self):
        for response in (b'\x0a\x0atruncated', integer(2, 2), blob(1, guild_row(name=b'\xff'))):
            with self.subTest(response=response):
                with self.assertRaises(GachaError) as error:
                    search_guilds(SearchSession(response), 'guild')
                self.assertEqual(error.exception.code, 'GUILD_SEARCH_PROTOCOL_ERROR')
