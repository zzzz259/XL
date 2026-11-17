"""Bounded read-only ranking collector using the statically confirmed client schema.

No authentication or transport lives here. The caller supplies a budgeted session.
COMPLETE_REPORTED_RANGE describes only the range reported by this endpoint.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime

from .gacha import GachaError, integer

MODE_RPC = {
    'guild_war': {'metadata': 2301, 'rank': 2307, 'rank_ext': 10033},
    'illusion': {'metadata': 2001, 'rank': 2002},
    'nightmare': {'metadata': 3001, 'rank': 3004},
}


class _ProtocolError(ValueError):
    pass


def _schema(*fields):
    return {number: (name, kind, repeated) for number, name, kind, repeated in fields}


def _scalar(number, name, kind='i32'):
    return number, name, kind, False


_GUILD_SCHEDULE = _schema(*[_scalar(n, k, t) for n, k, t in [
    (1,'seasonId','i32'), (2,'phase','i32'), (3,'state','i32'),
    (4,'startStamp','i64'), (5,'endStamp','i64')]])
_WORLD_SCHEDULE = dict(_GUILD_SCHEDULE)
_WORLD_SCHEDULE[6] = ('settle', 'i32', False)
_GUILD = _schema(*[_scalar(n, k, t) for n,k,t in [
    (1,'guildUid','i64'), (2,'level','i32'), (3,'iconId','i32'),
    (4,'name','str'), (5,'rank','i32'), (6,'score','i64'),
    (7,'memberCount','i32'), (8,'actCount','i32'), (9,'round','i32')]])
_MEMBER = _schema(_scalar(1,'uin','i64'), _scalar(2,'level'),
    _scalar(3,'name','str'), _scalar(4,'actorHead','opaque'),
    _scalar(5,'rank'), _scalar(6,'score','i64'))
_BOSS = _schema(*[_scalar(n,k,t) for n,k,t in [
    (1,'uin','i64'), (2,'level','i32'), (3,'faceId','i32'), (4,'name','str'),
    (5,'guildUid','i64'), (6,'guildName','str'), (7,'rank','i32'),
    (8,'score','i64'), (9,'scoreDiff','i32'), (10,'actorHead','opaque')]])
_WORLD = _schema(*[_scalar(n,k,t) for n,k,t in [
    (1,'uin','i64'), (2,'level','i32'), (3,'name','str'),
    (4,'actorHead','opaque'), (5,'rank','i32'), (6,'damage','i32'), (7,'guildName','str')]])
_GUILD_META = _schema(_scalar(1,'schedule',_GUILD_SCHEDULE), _scalar(2,'inGuild','bool'))
_WORLD_META = _schema(_scalar(1,'schedule',_WORLD_SCHEDULE))
_BOSS_META = _schema(*[_scalar(n,k,t) for n,k,t in [
    (1,'season','i32'), (2,'endStamp','i64'), (3,'nextStartStamp','i64'),
    (4,'fighterCount','i32'), (5,'challengeCount','i32'), (6,'highScore','i64'),
    (7,'curChapter','i32'), (8,'curStage','i32'), (9,'bossHpMax','i64'),
    (10,'bossHpCur','i64'), (11,'rank','i32'), (12,'rankRatio','i32'),
    (14,'isStart','bool'), (16,'highScoreDiff','i32'), (17,'phase','i32')]],
    (13,'challenge','opaque',True), (15,'rewardStages','i32',True))
_GUILD_PAGE = _schema((1,'guildList',_GUILD,True), (2,'memberList',_MEMBER,True),
    _scalar(3,'rank'), _scalar(4,'rankRatio'), _scalar(5,'mineGuild',_GUILD))
_BOSS_PAGE = _schema(_scalar(1,'rank'), _scalar(2,'rankRatio'),
    (3,'rankList',_BOSS,True), _scalar(4,'index'), _scalar(5,'rankCount'))
_WORLD_PAGE = _schema(_scalar(1,'startIndex'), _scalar(2,'totalRank'),
    _scalar(3,'schedule',_WORLD_SCHEDULE), (4,'rankList',_WORLD,True),
    _scalar(5,'rank'), _scalar(6,'rankRatio'), _scalar(7,'damage'))


def _varint(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise _ProtocolError()
        byte = data[offset]
        offset += 1
        if shift == 63 and byte > 1:
            raise _ProtocolError()
        value |= (byte & 127) << shift
        if not byte & 128:
            return value, offset
    raise _ProtocolError()


def _wire_fields(data, depth=0):
    if depth > 16 or len(data) > 4 * 1024 * 1024:
        raise _ProtocolError()
    offset = 0
    count = 0
    while offset < len(data):
        count += 1
        if count > 50000:
            raise _ProtocolError()
        tag, offset = _varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if not 0 < number < (1 << 29):
            raise _ProtocolError()
        if wire == 0:
            value, offset = _varint(data, offset)
        elif wire in (1, 2, 5):
            if wire == 2:
                length, offset = _varint(data, offset)
            else:
                length = 8 if wire == 1 else 4
            end = offset + length
            if end > len(data):
                raise _ProtocolError()
            value, offset = data[offset:end], end
        elif wire == 3:
            offset = _skip_group(data, offset, number, depth + 1)
            value = None
        else:
            raise _ProtocolError()
        yield number, wire, value


def _skip_group(data, offset, group_number, depth):
    if depth > 16:
        raise _ProtocolError()
    for _ in range(50000):
        tag, offset = _varint(data,offset)
        number, wire = tag >> 3, tag & 7
        if not 0 < number < (1 << 29):
            raise _ProtocolError()
        if wire == 4:
            if number != group_number:
                raise _ProtocolError()
            return offset
        if wire == 0:
            _, offset = _varint(data,offset)
        elif wire == 3:
            offset = _skip_group(data,offset,number,depth+1)
        elif wire in (1,2,5):
            if wire == 2:
                length,offset = _varint(data,offset)
            else:
                length = 8 if wire == 1 else 4
            offset += length
            if offset > len(data):
                raise _ProtocolError()
        else:
            raise _ProtocolError()
    raise _ProtocolError()


def _signed(value, bits):
    if bits == 32 and value > 0xffffffff and value < (1 << 64) - (1 << 31):
        raise _ProtocolError()
    value &= (1 << bits) - 1
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def _decode(data, schema, depth=0):
    if not isinstance(data, bytes) or depth > 16:
        raise _ProtocolError()
    result = {}
    for number, wire, value in _wire_fields(data, depth):
        field = schema.get(number)
        if field is None:
            continue
        name, kind, repeated = field
        if name in result and not repeated:
            raise _ProtocolError()
        if repeated and kind == 'i32' and wire == 2:
            packed, offset = [], 0
            while offset < len(value):
                raw, offset = _varint(value, offset)
                packed.append(_signed(raw, 32))
            result.setdefault(name, []).extend(packed)
            continue
        expected_wire = 2 if isinstance(kind, dict) or kind in ('str', 'opaque') else 0
        if wire != expected_wire:
            raise _ProtocolError()
        if isinstance(kind, dict):
            decoded = _decode(value, kind, depth + 1)
        elif kind == 'opaque':
            # Referenced message schema not included: validate framing, discard data.
            list(_wire_fields(value, depth + 1))
            decoded = None
        elif kind == 'str':
            try:
                decoded = value.decode('utf-8', errors='strict')
            except UnicodeError:
                raise _ProtocolError() from None
        elif kind == 'bool':
            if value not in (0, 1):
                raise _ProtocolError()
            decoded = bool(value)
        else:
            decoded = _signed(value, 64 if kind == 'i64' else 32)
        if repeated:
            result.setdefault(name, []).append(decoded)
        else:
            result[name] = decoded
    return result


def _metadata(mode, data, clock):
    decoded = _decode(data, {'guild_war':_GUILD_META, 'illusion':_BOSS_META, 'nightmare':_WORLD_META}[mode])
    if mode == 'illusion':
        decoded.pop('challenge', None)
        decoded.setdefault('isStart', False)
        metadata = decoded
        end, start = metadata.get('endStamp'), metadata.get('nextStartStamp')
        if end is None or start is None or not 0 < end < start or clock in (end, start) or clock > start:
            availability = 'UNKNOWN'
        else:
            availability = 'ACTIVE' if clock < end else 'CLOSED'
        season = metadata.get('season')
    else:
        metadata = decoded.get('schedule', {}).copy()
        if mode == 'guild_war' and 'inGuild' in decoded:
            metadata['inGuild'] = decoded['inGuild']
        if mode == 'guild_war':
            metadata['member_scope'] = 'UNKNOWN'
        states = {0:'NOT_OPEN',1:'NOTICE',2:'ACTIVE',3:'SETTLING',4:'SETTLED_CANDIDATE',5:'CLOSED'} if mode == 'guild_war' else {0:'NOT_OPEN',1:'NOTICE',2:'ACTIVE',3:'SETTLED_CANDIDATE',4:'CLOSED'}
        # Proto3 enum zero is legitimate even when omitted inside a schedule.
        availability = states.get(metadata.get('state', 0), 'UNKNOWN') if 'schedule' in decoded else 'UNKNOWN'
        if availability == 'ACTIVE':
            start, end = metadata.get('startStamp'),metadata.get('endStamp')
            if start is None or end is None or not 0 < start < clock < end:
                availability = 'UNKNOWN'
        season = metadata.get('seasonId')
    metadata['time_basis'] = 'LOCAL_CLOCK'
    return metadata, str(season) if season is not None and season > 0 else None, availability


def _entry(raw, kind):
    identifier = raw.get('guildUid' if kind == 'guild' else 'uin', 0)
    if identifier <= 0 or raw.get('rank', 0) <= 0 or raw.get('guildUid',0) < 0:
        raise _ProtocolError()
    clean = {key: value for key, value in raw.items() if key != 'actorHead'}
    # Identity fields remain decimal strings even inside the allowlisted raw record.
    for key in ('uin', 'guildUid'):
        if key in clean:
            clean[key] = str(clean[key])
    result = {'rank': raw['rank'], 'entity_id':str(identifier),
        'display_name':raw.get('name',''), 'score':raw.get('damage' if kind == 'nightmare' else 'score',0),
        'guild_uid':str(raw['guildUid']) if raw.get('guildUid',0) > 0 else None,
        'guild_name':raw.get('name','') if kind == 'guild' else raw.get('guildName'),
        'level':raw.get('level',0), 'raw_data':clean}
    if kind == 'guild_member':
        result.update(entity_kind='guild_member', scope='UNKNOWN')
    return result


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',',':')).encode()).hexdigest()


def collect_mode(session, mode, *, scope, first_page_only=False, max_pages=20,
                 max_entries=200, metadata_only=False, allow_closed=False,
                 now=time.time):
    """Collect one mode; transport errors retain partial records and fixed error codes.

    Pages include guild's ext comparison and consistency reads. Local clocks only
    provide provisional activity classification; static evidence is not live proof.
    Ranking pages are read only while the mode reports ACTIVE, SETTLING or
    SETTLED_CANDIDATE; callers serving closed-period archives must opt in with
    allow_closed=True, which never upgrades CLOSED availability and keeps
    settle_evidence at LAST_OBSERVED. Default budgets match the CLI (20 pages,
    200 entries); server collectors may raise them within the validated bound.
    """
    if mode not in MODE_RPC or not isinstance(scope,str) or not scope.strip() or len(scope) > 512:
        raise ValueError('explicit scope and supported mode required')
    if any(type(value) is not int or not 1 <= value <= 100000 for value in (max_pages,max_entries)):
        raise ValueError('positive bounded page and entry budgets required')
    if type(allow_closed) is not bool:
        raise ValueError('allow_closed must be an explicit bool')
    clock = now()
    result = {'scope':scope, 'mode':mode, 'season_id':None,
        'entity_kind':'guild' if mode == 'guild_war' else 'player',
        'metadata':{}, 'availability':'UNKNOWN', 'entries':[], 'member_entries':[],
        'page_evidence':[], 'coverage':'NO_RANKING', 'settle_evidence':'LAST_OBSERVED',
        'collection_error':None, 'pages_fetched':0,
        'collected_at_utc':datetime.fromtimestamp(clock, UTC).isoformat(),
        'source_rpc':[], 'source_hash':None}
    commands = MODE_RPC[mode]
    initial_index = 1 if mode == 'illusion' else 0
    seen_ids, seen_ranks = {}, {}
    member_ids, member_ranks = {}, {}

    def request(command, body=b''):
        result['source_rpc'].append(command)
        return session.request(command,body)

    def inconsistent():
        result['coverage'] = 'INCONSISTENT'

    def read_page(command, cursor=None, recheck=False):
        if cursor is None:
            cursor = initial_index
        data = request(command, b'' if mode == 'guild_war' else integer(1,cursor))
        result['pages_fetched'] += 1
        decoded = _decode(data, {'guild_war':_GUILD_PAGE,'illusion':_BOSS_PAGE,'nightmare':_WORLD_PAGE}[mode])
        entries = [_entry(row, 'guild' if mode == 'guild_war' else mode) for row in decoded.get('guildList' if mode == 'guild_war' else 'rankList', [])]
        members = [_entry(row,'guild_member') for row in decoded.get('memberList',[])]
        echo = decoded.get('index' if mode == 'illusion' else 'startIndex',0) if mode != 'guild_war' else None
        total = decoded.get('rankCount' if mode == 'illusion' else 'totalRank') if mode != 'guild_war' else None
        fingerprint = _hash({'entries':entries,'members':members,'echo':echo,'total':total,'schedule':decoded.get('schedule')})
        result['page_evidence'].append({'source_rpc':command,'request_index':cursor if mode != 'guild_war' else None,
            'echoed_index':echo,'reported_total':total,'entry_count':len(entries),'member_count':len(members),
            'first_rank':entries[0]['rank'] if entries else None,
            'last_rank':entries[-1]['rank'] if entries else None,
            'source_hash':fingerprint,'is_recheck':recheck})
        if mode != 'guild_war' and (echo != cursor or echo < initial_index or total is not None and (total < 0 or cursor + len(entries) > total + initial_index)):
            inconsistent()
        if mode != 'guild_war' and any(entry['rank'] != cursor-initial_index+offset+1 for offset,entry in enumerate(entries)):
            inconsistent()
        if mode == 'nightmare':
            embedded = decoded.get('schedule')
            original = {k:v for k,v in metadata.items() if k != 'time_basis'}
            if embedded is None or embedded != original:
                inconsistent()
        return entries, members, total, echo, fingerprint

    def append_rows(rows, members=False, allow_identical=False):
        target = result['member_entries' if members else 'entries']
        ids, ranks = (member_ids,member_ranks) if members else (seen_ids,seen_ranks)
        capped = False
        for entry in rows:
            eid, rank = entry['entity_id'],entry['rank']
            if eid in ids or rank in ranks:
                if allow_identical and ids.get(eid) == entry and ranks.get(rank) == eid:
                    continue
                inconsistent()
                return False
            if len(result['entries']) + len(result['member_entries']) >= max_entries:
                capped = True
                break
            if target and rank <= target[-1]['rank']:
                inconsistent()
                return False
            ids[eid],ranks[rank] = entry,eid
            target.append(entry)
        return not capped

    try:
        metadata,season,availability = _metadata(mode,request(commands['metadata']),clock)
        result.update(metadata=metadata.copy(),season_id=season,availability=availability)
        if availability == 'ACTIVE':
            result['settle_evidence'] = 'LIVE'
        elif availability == 'SETTLED_CANDIDATE':
            result['settle_evidence'] = 'SETTLED_CANDIDATE'
        if not metadata_only and season is not None and (
                availability in ('ACTIVE','SETTLING','SETTLED_CANDIDATE')
                or allow_closed and availability == 'CLOSED'):
            result['coverage'] = 'PARTIAL'
            rows,members,total,echo,first_hash = read_page(commands['rank'])
            accepted = result['coverage'] != 'INCONSISTENT' and append_rows(rows) and append_rows(members,True)
            if mode != 'guild_war' and not rows and total != 0:
                # A positive/unknown range with no progress is not a retry cue.
                accepted = False
            if accepted and mode == 'guild_war' and result['pages_fetched'] < max_pages:
                rows2,members2,_,_,_ = read_page(commands['rank_ext'])
                accepted = append_rows(rows2,allow_identical=True) and append_rows(members2,True,True)
                if accepted:
                    result['coverage'] = 'SERVER_VISIBLE_SLICE'
            if accepted and mode != 'guild_war' and not first_page_only:
                cursor = echo + len(rows)
                while total is not None and cursor < total + initial_index and result['pages_fetched'] < max_pages and len(result['entries']) + len(result['member_entries']) < max_entries:
                    rows,members,new_total,echo,_ = read_page(commands['rank'],cursor)
                    if result['coverage'] == 'INCONSISTENT' or new_total != total:
                        inconsistent()
                        accepted = False
                        break
                    if not rows:
                        accepted = False
                        break
                    if not append_rows(rows):
                        accepted = False
                        break
                    cursor = echo + len(rows)
                range_complete = total is not None and cursor == total + initial_index and len(result['entries']) == total
            else:
                range_complete = False
            if accepted and not first_page_only and result['coverage'] != 'INCONSISTENT' and result['pages_fetched'] < max_pages and len(result['entries']) + len(result['member_entries']) <= max_entries:
                # Do not spend consistency requests after truncating a visible range.
                can_check = mode == 'guild_war' or range_complete
                if can_check:
                    latest = _metadata(mode,request(commands['metadata']),now())
                    unchanged = latest == (metadata,season,availability)
                    result['metadata']['recheck'] = latest[0]
                    if not unchanged:
                        inconsistent()
                    else:
                        *_,last_hash = read_page(commands['rank'],recheck=True)
                        if first_hash != last_hash:
                            inconsistent()
                        elif range_complete and result['coverage'] != 'INCONSISTENT':
                            result['coverage'] = 'COMPLETE_REPORTED_RANGE'
                            if not result['entries']:
                                result['availability'] = 'EMPTY'
    except _ProtocolError:
        result['collection_error'] = 'LEADERBOARD_PROTOCOL_ERROR'
    except GachaError as exc:
        # Only fixed transport codes are exposed. Exception text is never retained.
        safe = {'REQUEST_BUDGET_EXCEEDED','REQUEST_LIMIT','REQUEST_LIMIT_EXCEEDED',
                'DURATION_LIMIT','DEADLINE_EXCEEDED','SESSION_DEADLINE_EXCEEDED',
                'RPC_REJECTED','SERVER_REJECTED','PROTOCOL_ERROR','REQUEST_TIMEOUT',
                'SESSION_CLOSED','CONNECTION_CLOSED','GATEWAY_REJECTED',
                'GATEWAY_RESPONSE_ERROR','RESPONSE_MISMATCH','UNSAFE_COMMAND',
                'SESSION_TIMEOUT','RESPONSE_TIMEOUT','RPC_NOT_ALLOWED','INVALID_FRAME',
                'GATEWAY_NETWORK_ERROR','RESPONSE_SIZE_LIMIT','RESPONSE_LIMIT'}
        result['collection_error'] = exc.code if exc.code in safe else 'LEADERBOARD_REQUEST_FAILED'
    except Exception:  # noqa: BLE001 - never persist raw decoder/transport exceptions
        result['collection_error'] = 'LEADERBOARD_REQUEST_FAILED'
    if result['collection_error'] and result['pages_fetched'] and result['coverage'] != 'INCONSISTENT':
        result['coverage'] = 'PARTIAL'
    result['metadata']['collection_started_at_utc'] = result['collected_at_utc']
    result['metadata']['collection_finished_at_utc'] = datetime.fromtimestamp(now(),UTC).isoformat()
    stable = {k:v for k,v in result.items() if k not in ('source_hash','collected_at_utc')}
    stable['metadata'] = {k:v for k,v in result['metadata'].items() if k not in ('collection_started_at_utc','collection_finished_at_utc')}
    result['source_hash'] = _hash(stable)
    return result
