"""Bounded Redis cache backed by the Option A PostgreSQL authority.

Cache sampling precedes the final authority check. Cache bytes, TTL, worker
health, and pubsub messages alone never establish LIVE confirmation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import time

from .market_stream import RedisMarketStreamState, stream_commit_version
from .stream_integrity import IntegrityRejection, timestamp_us, timestamp_from_us
from .stream_postgres_authority import PostgresAuthority, encode
from .stream_postgres_connection import create_authority_engine

STAGE_LUA = r'''
local prior = redis.call('GET', KEYS[1])
if prior then
    local oldticket = string.sub(prior, 1, 19)
    local ticket = string.sub(ARGV[1], 1, 19)
    if oldticket > ticket then return 'newer_slot' end
    if oldticket == ticket and prior ~= ARGV[1] then return 'slot_conflict' end
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
return 'staged'
'''


class PostgresMarketStreamState(RedisMarketStreamState):
    requires_authority = True

    def __init__(self, client, authority: PostgresAuthority, *, clock=None):
        # Do not initialize/read legacy Redis safety metadata or create schema.
        self.client, self.authority = client, authority
        self.namespace = authority.namespace
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _slot(self, number):
        return f'{self.namespace}:pgcache:{number}'

    def _stage(self, op, ttl_seconds):
        if type(ttl_seconds) is not int or not 0 < ttl_seconds <= 86400:
            raise IntegrityRejection('cache_ttl_invalid')
        raw = f"{op['ticket']:019d}|{encode(op)}"
        result = self.client.eval(STAGE_LUA, 1, self._slot(op['slot']), raw, ttl_seconds)
        if result not in ('staged', b'staged'):
            raise IntegrityRejection('cache_stage_conflict')
        return op['fingerprint']  # returned only after an exact successful response

    def set_latest(self, event, *, ttl_seconds, stale=False):
        started = time.perf_counter()
        version = stream_commit_version(event.stream_commit)
        if version is None:
            raise IntegrityRejection('legacy_writer_fenced')
        payload = event.serialized(); payload['is_stale'] = bool(stale)
        op = self.authority.reserve(f'{event.event_type}:{event.symbol}', timestamp_us(event.event_timestamp),
            payload, *version, now=timestamp_us(self.clock()), recovery=event.source_mode == 'rest')
        # No automatic retry of an unknown transaction outcome. Reservation is
        # durable before Redis. A failure at any later boundary retains the bound.
        if self._stage(op, ttl_seconds) != op['fingerprint']:
            raise IntegrityRejection('cache_stage_ack_mismatch')
        self.authority.confirm(op)
        return (time.perf_counter() - started) * 1000

    def reconcile_price(self, symbol, event_type, *, ttl_seconds=120):
        """Bounded explicit repair; never infer successful storage from visibility.

        A pending operation is staged AGAIN to obtain a fresh reply, then CAS
        confirmed. Already-confirmed heads can rebuild expired/rolled-back cache.
        Concurrent changes reject the repair; caller may retry later, never loop.
        """
        key = f'{event_type}:{symbol}'
        root, row = self.authority.read(key)
        if row is None or not (row['pending'] or row['confirmed']):
            raise IntegrityRejection('no_authoritative_repair_source')
        op = row['pending'] or row['confirmed']
        if op['generation'] != root['generation']:
            raise IntegrityRejection('no_current_authoritative_repair_source')
        if op['stamp'] <= row['price_floor'] or op['stamp'] < root['recovery_floor']:
            raise IntegrityRejection('repair_below_authoritative_price_floor')
        self._stage(op, ttl_seconds)
        if row['pending']:
            self.authority.confirm(op)
        after, current = self.authority.read(key)
        if after['generation'] != root['generation'] or current is None or current['confirmed'] != op:
            raise IntegrityRejection('cache_repair_raced_retry_later')
        return 'reconciled'

    def integrity_preflight(self, *, required=None, known_contexts=()):
        self.authority.read()
        return {'status': 'COMPATIBLE_VERIFIED_STATE', 'scope': 'option_a_postgresql'}

    def reconcile_integrity(self, *args, **kwargs):
        raise IntegrityRejection('postgres_initialization_requires_separate_operator_plan')

    def integrity_floor(self, symbol, event_type):
        root, row = self.authority.read(f'{event_type}:{symbol}')
        bound = max(row['bound'], row['price_floor'], root['recovery_floor']) if row else root['retired']
        return (timestamp_from_us(bound) if bound else None), row['uncertain'] if row else bool(bound)

    @staticmethod
    def _diagnostic(symbol, event_type, reason, payload=None):
        value = dict(payload or dict(symbol=symbol, event_type=event_type, payload={}, quality_flags=[]))
        value['_authority_unverified'] = reason
        return value

    def get_latest(self, symbol, event_type):
        key = f'{event_type}:{symbol}'
        try:
            _, first = self.authority.read(key)
            if first is None:
                return self._diagnostic(symbol, event_type, 'no_authoritative_price')
            slots = {}
            for number in (2 * first['position'], 2 * first['position'] + 1):
                raw = self.client.get(self._slot(number))
                if isinstance(raw, bytes): raw = raw.decode()
                slots[number] = json.loads(raw.split('|', 1)[1]) if raw else None
            root, row = self.authority.read(key)  # fresh, after cache sample
            head = row['confirmed'] if row else None
            if head is None or head['generation'] != root['generation']:
                return self._diagnostic(symbol, event_type, 'no_current_confirmation')
            if slots.get(head['slot']) != head:
                return self._diagnostic(symbol, event_type, 'cache_authority_mismatch', head['payload'])
            result = dict(head['payload'])
            result['_authority_ticket'] = head['ticket']
            return result
        except Exception:
            return self._diagnostic(symbol, event_type, 'authority_or_cache_unavailable')

    def event_quality(self, event):
        if event.get('_authority_unverified'):
            return 'UNKNOWN'
        try:
            root, row = self.authority.read(f"{event['event_type']}:{event['symbol']}")
            head = row['confirmed'] if row else None
            payload = {k:v for k,v in event.items() if k != '_authority_ticket'}
            if (head is None or head['generation'] != root['generation']
                    or head['ticket'] != event.get('_authority_ticket') or payload != head['payload']):
                return 'UNKNOWN'
            if head['stamp'] <= row['price_floor'] or head['stamp'] < root['recovery_floor']:
                return 'UNKNOWN'  # retained historical price cannot satisfy reentry
            if row['uncertain']:
                return 'UNKNOWN'  # known unresolved work visible immediately
            if row['stale']:
                return 'STALE'
            return 'DEGRADED' if row['hold'] else 'LIVE'
        except Exception:
            return 'UNKNOWN'

    def integrity_quality(self, symbol, event_type, stamp, version):
        event = self.get_latest(symbol, event_type)
        if (event.get('event_timestamp') != stamp.isoformat()
                or stream_commit_version(event.get('stream_commit')) != version):
            return 'UNKNOWN'
        return self.event_quality(event)

    def hold_quality(self, symbol, event_type, owner, serial, reason):
        # Transactional generation fencing; repeated holds remain conservative.
        self.authority.hold(f'{event_type}:{symbol}', reason, owner=owner)

    def aggregate_context(self, symbol, event_type):
        root, row = self.authority.read(f'{event_type}:{symbol}')
        op = row['confirmed'] if row else None
        if op is None:
            context = row.get('context') if row else None
            if context:
                return (timestamp_from_us(context['stamp']), context['sequence'], context['event_id'], None)
            return None
        # Ordering context may survive a generation transition; this does NOT
        # make the old generation's price a current confirmation.
        payload = op['payload']
        return (timestamp_from_us(op['stamp']), payload['sequence_number'], payload['provider_event_id'], None)

    def mark_symbols_stale(self, symbols, *, reason, ttl_seconds):
        for symbol in set(symbols):
            for channel in ('Q', 'T', 'A', 'AM'):
                key = f'{channel}:{symbol}'
                _, row = self.authority.read(key)
                head = row['confirmed'] if row else None
                if head:
                    # If a new price commits after this read, stale CAS cannot
                    # mark it. No read/modify/write of rollbackable cache bytes.
                    self.authority.hold(key, reason, expected_ticket=head['ticket'], stale=True)


def from_environment(redis_url):
    """Explicit opt-in; both web readers and worker must select the same manifest."""
    import redis
    path = os.environ.get('MASSIVE_STREAM_POSTGRES_SELECTION_FILE')
    if not path:
        raise IntegrityRejection('postgres_selection_file_required')
    with open(path, encoding='utf-8') as handle:
        selection = json.load(handle)
    database_url = os.environ.get('DATABASE_URL', '')
    engine = create_authority_engine(database_url, selection)
    authority = PostgresAuthority(engine, selection)
    client = redis.Redis.from_url(redis_url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
    return PostgresMarketStreamState(client, authority)
