"""Offline protocol cases use the extracted 2026-10-12 client schema."""
import json
import unittest
from pathlib import Path

try:
    from gacha_core import leaderboards as lb
except ImportError:
    lb = None
from gacha_core.gacha import GachaError
from gacha_core.gacha import blob as b
from gacha_core.gacha import integer as i


def schedule(season=42, state=2, **kw):
    return i(1, season) + i(2, 3) + i(3, state) + i(4, kw.get('start', 50)) + i(5, kw.get('end', 150))


def meta(mode, season=42, state=2):
    if mode == 'illusion':
        return i(1, season) + i(2, 150) + i(3, 250) + i(14, 0) + i(17, 3)
    return b(1, schedule(season, state))


def actor(mode, uid=9007199254740993, rank=1, score=None):
    if score is None:
        score = 3 if mode == 'nightmare' else 9223372036854775807
    if mode == 'illusion':
        return i(1, uid) + i(2, 60) + b(4, '角色'.encode()) + i(5, 9007199254740995) + b(6, b'guild') + i(7, rank) + i(8, score)
    return i(1, uid) + i(2, 60) + b(3, b'actor') + i(5, rank) + i(6, score)


def page(mode, rows=(), cursor=None, total=1, season=42):
    if cursor is None:
        cursor = 1 if mode == 'illusion' else 0
    if mode == 'illusion':
        return b''.join(b(3, row) for row in rows) + i(4, cursor) + i(5, total)
    if mode == 'nightmare':
        return i(1, cursor) + i(2, total) + b(3, schedule(season)) + b''.join(b(4, row) for row in rows)
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


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(lb, 'leaderboard protocol collector is not implemented')

    def collect(self, session, mode='illusion', **kw):
        return lb.collect_mode(session, mode, scope='cn:test-server:account-context', now=lambda: 100, **kw)

    def test_public_rpc_mapping_matches_fixed_evidence(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures' / 'leaderboard_protocol.json').read_text())
        self.assertEqual(lb.MODE_RPC, fixture['rpc'])

    def test_fixed_hex_messages_are_decoded_from_client_field_numbers(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures' / 'leaderboard_protocol.json').read_text())
        for mode, key in [('illusion','fixed_boss_rank_actor_hex'), ('nightmare','fixed_world_boss_rank_hex'), ('guild_war','fixed_guild_rank_hex')]:
            with self.subTest(mode=mode):
                row = bytes.fromhex(fixture[key])
                responses = [meta(mode), page(mode,[row])]
                if mode == 'guild_war':
                    responses.append(page(mode,[row]))
                r = self.collect(FakeSession(*responses), mode, first_page_only=True)
                self.assertIsNone(r['collection_error'])
                self.assertEqual((r['entries'][0]['entity_id'],r['entries'][0]['rank'],r['entries'][0]['score']), ('1',1,3))

    def test_unknown_groups_are_skipped_with_matching_end_tag(self):
        group = bytes.fromhex('a3060801a406')  # unknown field 100, group start/end
        s = FakeSession(meta('illusion')+group, page('illusion',[actor('illusion')])+group)
        self.assertIsNone(self.collect(s,first_page_only=True)['collection_error'])
        for invalid in [bytes.fromhex('a3060801ac06'), bytes.fromhex('a3060801'), bytes.fromhex('a406')]:
            with self.subTest(invalid=invalid):
                self.assertEqual(self.collect(FakeSession(meta('illusion')+invalid))['collection_error'], 'LEADERBOARD_PROTOCOL_ERROR')

    def test_proto3_zero_total_can_be_omitted_without_claiming_complete(self):
        s = FakeSession(meta('illusion'), i(4,1))
        r = self.collect(s,first_page_only=True)
        self.assertEqual(r['coverage'],'PARTIAL')
        self.assertEqual(r['availability'],'ACTIVE')

    def test_normal_guild_compares_first_page_within_budget(self):
        row = i(1,77)+i(5,1)+i(6,3)
        p = page('guild_war',[row])
        s = FakeSession(meta('guild_war'),p,p,meta('guild_war'),p)
        r = self.collect(s,'guild_war')
        self.assertEqual(r['coverage'],'SERVER_VISIBLE_SLICE')
        self.assertEqual(r['pages_fetched'],3)
        self.assertEqual(len(s.calls),5)

    def test_active_enum_conflicting_with_schedule_boundaries_is_unknown(self):
        for mode in ('guild_war','nightmare'):
            for start,end in [(100,150),(50,100),(101,150),(50,99),(150,50)]:
                with self.subTest(mode=mode,start=start,end=end):
                    s = FakeSession(b(1,schedule(start=start,end=end)))
                    r = self.collect(s,mode)
                    self.assertEqual(r['availability'],'UNKNOWN')
                    self.assertEqual(len(s.calls),1)

    def test_negative_guild_identity_is_rejected(self):
        row = i(1,1)+i(5,-1)+i(7,1)
        r = self.collect(FakeSession(meta('illusion'),page('illusion',[row])),first_page_only=True)
        self.assertEqual(r['collection_error'],'LEADERBOARD_PROTOCOL_ERROR')

    def test_session_fixed_timeout_codes_are_preserved(self):
        for code in ('SESSION_TIMEOUT','RESPONSE_TIMEOUT','RPC_NOT_ALLOWED','INVALID_FRAME','GATEWAY_NETWORK_ERROR','RESPONSE_SIZE_LIMIT','RESPONSE_LIMIT','RANKING_REJECTED'):
            with self.subTest(code=code):
                r = self.collect(FakeSession(GachaError(code)))
                self.assertEqual(r['collection_error'],code if code != 'RANKING_REJECTED' else 'LEADERBOARD_REQUEST_FAILED')

    def test_known_nested_metadata_wire_types_are_checked(self):
        for mode,body in [('guild_war',b(1,b(1,b'42'))),('nightmare',b(1,i(3,2)+i(3,3))),('illusion',i(14,2)),('illusion',i(2,1<<40)+i(3,(1<<40)+10)+b(15,b'\x80'))]:
            with self.subTest(mode=mode):
                r = self.collect(FakeSession(body),mode,first_page_only=True)
                self.assertEqual(r['collection_error'],'LEADERBOARD_PROTOCOL_ERROR')

    def test_signed_packed_and_unpacked_metadata_values(self):
        body = meta('illusion')+b(15,b'\x01\x02')+i(15,-1)+i(16,-2147483648)
        r = self.collect(FakeSession(body,page('illusion',[actor('illusion')])),first_page_only=True)
        self.assertEqual(r['metadata']['rewardStages'],[1,2,-1])
        self.assertEqual(r['metadata']['highScoreDiff'],-2147483648)

    def test_source_hash_excludes_timestamp_and_unknown_payloads(self):
        rows = page('illusion',[actor('illusion')])
        first = self.collect(FakeSession(meta('illusion'),rows),first_page_only=True)
        second = lb.collect_mode(FakeSession(meta('illusion')+b(99,b'private'),rows), 'illusion', scope=first['scope'],now=lambda:101,first_page_only=True)
        self.assertEqual(first['source_hash'],second['source_hash'])
        self.assertNotEqual(first['collected_at_utc'],second['collected_at_utc'])

    def test_total_change_and_empty_nonterminal_page_stop(self):
        first = page('illusion',[actor('illusion')],total=3)
        for second, expected in [(page('illusion',[],cursor=2,total=3),'PARTIAL'),(page('illusion',[actor('illusion',uid=2,rank=2)],cursor=2,total=4),'INCONSISTENT')]:
            with self.subTest(second=second):
                s = FakeSession(meta('illusion'),first,second)
                self.assertEqual(self.collect(s)['coverage'],expected)
                self.assertEqual(len(s.calls),3)

    def test_empty_first_page_with_positive_total_stops_without_repeating_cursor(self):
        for mode in ('illusion', 'nightmare'):
            with self.subTest(mode=mode):
                s = FakeSession(meta(mode), page(mode, [], total=3))
                r = self.collect(s, mode)
                self.assertEqual(len(s.calls), 2)
                self.assertEqual(r['coverage'], 'PARTIAL')
                self.assertIsNone(r['collection_error'])

    def test_metadata_only_active_modes_never_request_ranking(self):
        for mode in ('guild_war', 'illusion', 'nightmare'):
            with self.subTest(mode=mode):
                s = FakeSession(meta(mode))
                r = self.collect(s, mode, metadata_only=True)
                self.assertEqual(len(s.calls), 1)
                self.assertEqual(r['availability'], 'ACTIVE')
                self.assertEqual(r['coverage'], 'NO_RANKING')
                self.assertEqual(r['pages_fetched'], 0)

    def test_illusion_first_page_preserves_exact_int64_and_guild_identity(self):
        s = FakeSession(meta('illusion'), page('illusion', [actor('illusion')]))
        result = self.collect(s, first_page_only=True)
        entry = result['entries'][0]
        self.assertEqual((entry['entity_id'], entry['guild_uid'], entry['score']), ('9007199254740993', '9007199254740995', 9223372036854775807))
        self.assertEqual(entry['display_name'], '角色')
        self.assertEqual(result['season_id'], '42')
        self.assertEqual(result['availability'], 'ACTIVE')
        self.assertEqual(result['coverage'], 'PARTIAL')
        self.assertEqual(s.calls, [(2001, b''), (2002, i(1, 1))])
        self.assertEqual(result['metadata']['isStart'], False)
        self.assertEqual(result['metadata']['time_basis'], 'LOCAL_CLOCK')
        self.assertEqual(result['settle_evidence'], 'LIVE')
        self.assertEqual(result['pages_fetched'], 1)
        self.assertEqual(len(result['source_hash']), 64)

    def test_guild_requests_are_empty_and_ext_retains_member_scope(self):
        guild = i(1, 77) + i(2, 12) + b(4, b'guild') + i(5, 1) + i(6, 2**60)
        member = actor('nightmare', score=-12)
        s = FakeSession(meta('guild_war'), page('guild_war', [guild]), b(1, guild) + b(2, member))
        r = self.collect(s, 'guild_war', first_page_only=True)
        self.assertEqual(s.calls, [(2301, b''), (2307, b''), (10033, b'')])
        self.assertEqual(r['coverage'], 'SERVER_VISIBLE_SLICE')
        self.assertEqual(len(r['entries']), 1)
        self.assertEqual(r['member_entries'][0]['score'], -12)
        self.assertEqual(r['member_entries'][0]['entity_kind'], 'guild_member')
        self.assertIsNone(r['member_entries'][0]['guild_uid'])
        self.assertEqual(r['metadata'].get('member_scope'),'UNKNOWN')
        self.assertEqual(r['page_evidence'][1].get('member_count'),1)

    def test_nightmare_damage_is_signed_int32(self):
        s = FakeSession(meta('nightmare'), page('nightmare', [actor('nightmare', score=-2147483648)]))
        r = self.collect(s, 'nightmare', first_page_only=True)
        self.assertEqual(r['entries'][0]['score'], -2147483648)

    def test_illusion_closed_window_does_not_query_rank(self):
        s = FakeSession(meta('illusion'))
        r = lb.collect_mode(s, 'illusion', scope='x', now=lambda: 200)
        self.assertEqual(r['availability'], 'CLOSED')
        self.assertEqual(r['coverage'], 'NO_RANKING')
        self.assertEqual(len(s.calls), 1)

    def test_illusion_time_boundaries_or_missing_times_are_unknown(self):
        for now, body in [(150, meta('illusion')), (250, meta('illusion')), (100, i(1, 42)), (100, i(1, 42) + i(2, 150) + i(3, 100))]:
            with self.subTest(now=now, body=body):
                s = FakeSession(body)
                r = lb.collect_mode(s, 'illusion', scope='x', now=lambda now=now: now)
                self.assertEqual(r['availability'], 'UNKNOWN')
                self.assertEqual(len(s.calls), 1)

    def test_missing_season_never_fetches_or_fabricates_zero(self):
        s = FakeSession(i(2, 150) + i(3, 250))
        r = self.collect(s)
        self.assertIsNone(r['season_id'])
        self.assertEqual(r['entries'], [])
        self.assertEqual(len(s.calls), 1)

    def test_known_wire_type_duplicate_utf8_and_truncation_fail_safely(self):
        malformed = [b(1, b'42'), i(1, 42) + i(1, 43), b'\x80', b'\x00', b'\x08' + b'\xff' * 10]
        for body in malformed:
            with self.subTest(body=body):
                r = self.collect(FakeSession(body), first_page_only=True)
                self.assertEqual(r['collection_error'], 'LEADERBOARD_PROTOCOL_ERROR')
        for row in [i(1, 1) + b(4, b'\xff'), actor('illusion') + i(7, 2), actor('illusion') + b(8, b'bad')]:
            with self.subTest(row=row):
                r = self.collect(FakeSession(meta('illusion'), page('illusion', [row])), first_page_only=True)
                self.assertEqual(r['collection_error'], 'LEADERBOARD_PROTOCOL_ERROR')

    def test_unknown_fields_skip_all_supported_wire_types(self):
        unknown = i(100, 5) + b(101, b'secret is discarded') + bytes.fromhex('b50601020304b9060102030405060708')
        r = self.collect(FakeSession(meta('illusion') + unknown, page('illusion', [actor('illusion') + unknown])), first_page_only=True)
        self.assertIsNone(r['collection_error'])
        self.assertNotIn('secret', json.dumps(r))

    def test_complete_range_requires_metadata_and_first_page_recheck(self):
        first = page('illusion', [actor('illusion')], total=2)
        second = page('illusion', [actor('illusion', uid=2, rank=2)], cursor=2, total=2)
        s = FakeSession(meta('illusion'), first, second, meta('illusion'), first)
        r = self.collect(s)
        self.assertEqual(r['coverage'], 'COMPLETE_REPORTED_RANGE')
        self.assertEqual(r['pages_fetched'], 3)
        self.assertEqual(s.calls[2], (2002, i(1, 2)))
        self.assertTrue(r['page_evidence'][-1]['is_recheck'])

    def test_valid_empty_rank_requires_explicit_range_and_consistency(self):
        p = page('illusion', [], total=0)
        r = self.collect(FakeSession(meta('illusion'), p, meta('illusion'), p))
        self.assertEqual(r['availability'], 'EMPTY')
        self.assertEqual(r['coverage'], 'COMPLETE_REPORTED_RANGE')
        r = self.collect(FakeSession(meta('illusion'), b''), first_page_only=True)
        self.assertNotEqual(r['availability'], 'EMPTY')

    def test_duplicate_conflict_and_wrong_echo_stop_before_next_page(self):
        first = page('illusion', [actor('illusion')], total=3)
        for second in [page('illusion', [actor('illusion')], cursor=2, total=3), page('illusion', [actor('illusion', uid=2, rank=1)], cursor=2, total=3), page('illusion', [actor('illusion', uid=2, rank=2)], cursor=1, total=3)]:
            with self.subTest(second=second):
                s = FakeSession(meta('illusion'), first, second)
                r = self.collect(s)
                self.assertEqual(r['coverage'], 'INCONSISTENT')
                self.assertEqual(len(s.calls), 3)

    def test_page_and_entry_caps_never_claim_complete(self):
        p = page('illusion', [actor('illusion'), actor('illusion', uid=2, rank=2)], total=2)
        for limits in [{'max_pages': 1}, {'max_entries': 1}]:
            with self.subTest(limits=limits):
                s = FakeSession(meta('illusion'), p)
                r = self.collect(s, **limits)
                self.assertEqual(r['coverage'], 'PARTIAL')
                self.assertEqual(len(s.calls), 2)
                self.assertLessEqual(len(r['entries']), limits.get('max_entries', 2))

    def test_season_total_or_first_page_changes_are_inconsistent(self):
        p = page('illusion', [actor('illusion')])
        for responses in [(meta('illusion', season=43),), (meta('illusion'), page('illusion', [actor('illusion', score=7)])), (meta('illusion'), page('illusion', [actor('illusion')], total=2))]:
            with self.subTest(responses=responses):
                r = self.collect(FakeSession(meta('illusion'), p, *responses))
                self.assertEqual(r['coverage'], 'INCONSISTENT')

    def test_metadata_recheck_and_observation_interval_are_retained(self):
        p = page('illusion',[actor('illusion')])
        s = FakeSession(meta('illusion'),p,meta('illusion',season=43))
        r = self.collect(s)
        self.assertEqual(r['season_id'],'42')
        self.assertEqual(r['metadata']['season'],42)
        self.assertEqual(r['metadata'].get('recheck',{}).get('season'),43)
        self.assertEqual(r['metadata']['collection_started_at_utc'],r['collected_at_utc'])
        self.assertIn('collection_finished_at_utc',r['metadata'])

    def test_nightmare_embedded_season_change_is_inconsistent(self):
        r = self.collect(FakeSession(meta('nightmare'), page('nightmare', [actor('nightmare')], season=43)), 'nightmare')
        self.assertEqual(r['coverage'], 'INCONSISTENT')

    def test_budget_error_retains_partial_without_secret_exception_text(self):
        s = FakeSession(meta('illusion'), page('illusion', [actor('illusion')], total=2), GachaError('REQUEST_BUDGET_EXCEEDED'))
        r = self.collect(s)
        self.assertEqual(len(r['entries']), 1)
        self.assertEqual(r['collection_error'], 'REQUEST_BUDGET_EXCEEDED')
        self.assertEqual(r['coverage'], 'PARTIAL')
        r = self.collect(FakeSession(RuntimeError('TOKEN=private')))
        self.assertEqual(r['collection_error'], 'LEADERBOARD_REQUEST_FAILED')
        self.assertNotIn('private', json.dumps(r))

    def test_enum_availability_keeps_raw_values_and_never_verifies_settlement(self):
        for mode, states in [('guild_war', {0:'NOT_OPEN',1:'NOTICE',3:'SETTLING',4:'SETTLED_CANDIDATE',5:'CLOSED',99:'UNKNOWN'}), ('nightmare', {0:'NOT_OPEN',1:'NOTICE',3:'SETTLED_CANDIDATE',4:'CLOSED',99:'UNKNOWN'})]:
            for state, expected in states.items():
                with self.subTest(mode=mode, state=state):
                    s = FakeSession(meta(mode, state=state), b'', b'')
                    r = self.collect(s, mode, first_page_only=True)
                    self.assertEqual(r['availability'], expected)
                    self.assertEqual(r['metadata']['state'], state)
                    self.assertNotEqual(r['settle_evidence'], 'SETTLED_VERIFIED')

    def test_requires_explicit_nonempty_scope_and_valid_budgets(self):
        for kw in [{'scope':''}, {'scope':'x','max_pages':0}, {'scope':'x','max_entries':0}, {'scope':'x','max_pages':True}]:
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                lb.collect_mode(FakeSession(), 'illusion', **kw)

    def test_reported_count_boundaries_for_one_based_and_zero_based_modes(self):
        for mode in ('illusion','nightmare'):
            initial = 1 if mode == 'illusion' else 0
            for total in (1,10,11,20):
                with self.subTest(mode=mode,total=total):
                    pages = []
                    for offset in range(0,total,10):
                        rows = [actor(mode,uid=rank,rank=rank) for rank in range(offset+1,min(offset+11,total+1))]
                        pages.append(page(mode,rows,cursor=initial+offset,total=total))
                    s = FakeSession(meta(mode),*pages,meta(mode),pages[0])
                    r = self.collect(s,mode)
                    self.assertEqual(r['coverage'],'COMPLETE_REPORTED_RANGE')
                    self.assertEqual([row['rank'] for row in r['entries']],list(range(1,total+1)))
                    self.assertEqual(s.calls[1],(lb.MODE_RPC[mode]['rank'],i(1,initial)))
                    self.assertEqual(r['pages_fetched'],len(pages)+1)

    def test_rank_gaps_or_rows_exceeding_reported_count_are_inconsistent(self):
        for mode in ('illusion','nightmare'):
            for rows,total in [([actor(mode,rank=2)],1),([actor(mode),actor(mode,uid=2,rank=2)],1)]:
                with self.subTest(mode=mode,rows=rows):
                    s = FakeSession(meta(mode),page(mode,rows,total=total))
                    self.assertEqual(self.collect(s,mode)['coverage'],'INCONSISTENT')
                    self.assertEqual(len(s.calls),2)


if __name__ == '__main__':
    unittest.main()
