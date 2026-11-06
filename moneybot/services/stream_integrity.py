"""Bounded price-time safety records, independent of expiring latest values.

The retirement floor is deliberately conservative: a forgotten channel must
advance beyond every retired bound. It is not a SIP sequence or reset epoch.
"""
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

MAX_WATERMARKS = 1024
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class IntegrityRejection(RuntimeError):
    """Storage verified that this proposal cannot commit."""


def timestamp_us(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('aware event time required')
    delta = value.astimezone(timezone.utc) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def timestamp_from_us(value: int) -> datetime:
    return EPOCH + timedelta(microseconds=value)


@dataclass(frozen=True)
class Watermark:
    reserved_us: int
    committed_us: int
    status: str
    token: str

    def encode(self) -> str:
        return f'{self.reserved_us}|{self.committed_us}|{self.status}|{self.token}'

    @classmethod
    def decode(cls, value: str) -> 'Watermark':
        try:
            reserved, committed, status, token = value.split('|', 3)
            result = cls(int(reserved), int(committed), status, token)
        except (AttributeError, TypeError, ValueError) as exc:
            raise IntegrityRejection('invalid_integrity_record') from exc
        if result.reserved_us < result.committed_us or result.committed_us < 0 or status not in {'u', 'c'}:
            raise IntegrityRejection('invalid_integrity_record')
        return result


class MemoryWatermarks:
    """Offline/in-memory repository equivalent of the persistent Redis journal."""
    def __init__(self, capacity: int = MAX_WATERMARKS):
        self.capacity = max(1, capacity)
        self.records: OrderedDict[str, Watermark] = OrderedDict()
        self.retired_us = 0

    def floor(self, field: str) -> tuple[int, bool]:
        current = self.records.get(field)
        return (current.reserved_us, current.status == 'u') if current else (self.retired_us, bool(self.retired_us))

    def reserve(self, field: str, when: int, token: str, *, recovery: bool = False) -> None:
        previous = self.records.get(field)
        floor, uncertain = self.floor(field)
        if when < floor or (when == floor and (uncertain or recovery)):
            raise IntegrityRejection('timestamp_integrity_hold')
        if previous and when == floor:
            owner, revision = token.rsplit(':', 1)
            prior_owner, prior_revision = previous.token.rsplit(':', 1)
            if owner != prior_owner or int(revision) <= int(prior_revision):
                raise IntegrityRejection('equal_time_unbound_ordering')
        if previous is None and len(self.records) >= self.capacity:
            retired_field = next(iter(self.records))
            self.retired_us = max(self.retired_us, self.records[retired_field].reserved_us)
            del self.records[retired_field]
        self.records[field] = Watermark(when, previous.committed_us if previous else 0, 'u', token)
        self.records.move_to_end(field)

    def commit(self, field: str, when: int, token: str) -> None:
        current = self.records.get(field)
        if current is None or current != Watermark(when, current.committed_us, 'u', token):
            raise IntegrityRejection('integrity_reservation_changed')
        self.records[field] = Watermark(when, when, 'c', token)


# Redis executes this single script serially. Every safety reservation/retired
# bound is written BEFORE the latest price; a command error cannot write a price
# and then erase its prior safety floor. A lost reply is still an unknown client
# outcome; a later read can verify the stored token and committed status.
# KEYS: latest, watermark hash, bounded LRU sorted set, quality, control, context.
# ARGV: field, source micros, token, JSON value, TTL, capacity, recovery flag,
#       legacy observed floor, Q/T/A/AM field names, context, expected generation.
COMMIT_LUA = r'''
local control = redis.call('GET', KEYS[5])
if not control or not string.match(control, '^ready|2|') then return 'integrity_not_ready' end
if control ~= ARGV[14] then return 'integrity_generation_fenced' end
for i=2,6 do if redis.call('PTTL', KEYS[i]) >= 0 then return 'authority_has_ttl' end end
local field, stamp, token = ARGV[1], tonumber(ARGV[2]), ARGV[3]
if not stamp or stamp < 0 or stamp > 9007199254740991 then return 'unsupported_timestamp_range' end
local capacity, recovery = tonumber(ARGV[6]), ARGV[7] == '1'
local schema = redis.call('HGET', KEYS[2], '__schema__')
if not schema then
    return 'integrity_state_missing'
elseif schema ~= control then
    return 'invalid_integrity_schema'
end
if redis.call('HLEN', KEYS[2]) - 3 ~= redis.call('ZCARD', KEYS[3]) then return 'invalid_integrity_index' end
if redis.call('HLEN', KEYS[4]) ~= redis.call('ZCARD', KEYS[3]) then return 'invalid_confirmation_index' end
local function decode(value)
    if not value then return nil end
    local reserved, committed, status, identity = string.match(value, '^(%d+)|(%d+)|([uc])|(.+)$')
    if not reserved or tonumber(reserved) < tonumber(committed) then error('invalid_integrity_record') end
    if status == 'c' and reserved ~= committed then error('invalid_integrity_record') end
    if not string.match(identity, '^[0-9a-f%-]+:%d+$') then error('invalid_integrity_record') end
    return {tonumber(reserved), tonumber(committed), status, identity}
end
local previous = decode(redis.call('HGET', KEYS[2], field))
local member = redis.call('ZSCORE', KEYS[3], field)
if (previous and not member) or (not previous and member) then return 'invalid_integrity_index' end
local retired_raw, ordinal_raw = redis.call('HGET', KEYS[2], '__retired__'), redis.call('HGET', KEYS[2], '__ordinal__')
if not retired_raw or not ordinal_raw or not string.match(retired_raw, '^%d+$') or not string.match(ordinal_raw, '^%d+$') then return 'invalid_integrity_record' end
local retired = tonumber(retired_raw)
if retired > 9007199254740991 or tonumber(ordinal_raw) >= 9007199254740991 then return 'invalid_integrity_record' end
local floor = previous and previous[1] or retired
local uncertain = previous and previous[3] == 'u' or (not previous and retired > 0)
floor = math.max(floor, tonumber(ARGV[8]))
if stamp < floor or (stamp == floor and (uncertain or recovery)) then return 'timestamp_integrity_hold' end
if previous and stamp == floor then
    local owner, revision = string.match(token, '^(.+):(%d+)$')
    local oldowner, oldrevision = string.match(previous[4], '^(.+):(%d+)$')
    if not owner or not oldowner or owner ~= oldowner or tonumber(revision) <= tonumber(oldrevision) then
        return 'equal_time_unbound_ordering'
    end
end
if recovery then
    for index = 9, 12 do
        local bound = decode(redis.call('HGET', KEYS[2], ARGV[index]))
        local value = bound and bound[1] or retired
        if value > stamp or (value == stamp and ARGV[index] == field) then return 'recovery_integrity_hold' end
    end
end
if not previous and redis.call('ZCARD', KEYS[3]) >= capacity then
    local oldest = redis.call('ZRANGE', KEYS[3], 0, 0)[1]
    if not oldest then error('invalid_integrity_index') end
    local old = decode(redis.call('HGET', KEYS[2], oldest))
    if not old then error('invalid_integrity_index') end
    -- Preserve the conservative bound before any deletion can occur.
    redis.call('HSET', KEYS[2], '__retired__', string.format('%.0f', math.max(retired, old[1])))
    redis.call('HDEL', KEYS[2], oldest)
    redis.call('HDEL', KEYS[4], oldest)
    redis.call('HDEL', KEYS[6], oldest)
    redis.call('ZREM', KEYS[3], oldest)
end
local ordinal = redis.call('HINCRBY', KEYS[2], '__ordinal__', 1)
local committed = previous and previous[2] or 0
local reservation = string.format('%.0f|%.0f|u|%s', stamp, committed, token)
redis.call('HSET', KEYS[2], field, reservation)
redis.call('ZADD', KEYS[3], ordinal, field)
redis.call('HSET', KEYS[4], field, token .. '|0|pending|write_ack_pending')
redis.call('SET', KEYS[1], ARGV[4], 'EX', ARGV[5])
if redis.call('HGET', KEYS[6], field) then redis.call('HSET', KEYS[6], field, ARGV[13]) end
redis.call('HSET', KEYS[2], field, string.format('%.0f|%.0f|c|%s', stamp, stamp, token))
return 'committed'
'''

# Marking a snapshot stale must not write that snapshot back over a newer price.
STALE_LUA = r'''
local control = redis.call('GET', KEYS[2])
if not control or not string.match(control, '^ready|2|') then return 'integrity_not_ready' end
if control ~= ARGV[4] then return 'integrity_generation_fenced' end
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 'changed' end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return 'marked'
'''

# A price is reader-confirmed only after its writer received COMMIT_LUA's reply.
# Late quality messages cannot change another price token or reverse a channel's
# quality sequence. A periodic health publication never edits this record.
QUALITY_LUA = r'''
local control = redis.call('GET', KEYS[3])
if not control or not string.match(control, '^ready|2|') then return 'integrity_not_ready' end
if control ~= ARGV[6] then return 'integrity_generation_fenced' end
local record = redis.call('HGET', KEYS[1], ARGV[1])
if not record then return 'missing' end
local token = string.match(record, '^%d+|%d+|[uc]|(.+)$')
if token ~= ARGV[2] then return 'superseded' end
local old = redis.call('HGET', KEYS[2], ARGV[1])
if not old then return 'missing' end
local prior, serial, state = string.match(old, '^([^|]+)|(%d+)|([^|]+)|')
if prior ~= token then return 'superseded' end
if ARGV[4] == 'confirmed' then
    if state ~= 'pending' or tonumber(serial) ~= 0 then return 'superseded' end
    if not string.match(record, '^%d+|%d+|c|') then return 'unconfirmed' end
elseif tonumber(ARGV[3]) <= tonumber(serial) then return 'superseded' end
local nextstate = ARGV[4]
if nextstate == 'held' and (state == 'pending' or state == 'uncertain') then nextstate = 'uncertain' end
redis.call('HSET', KEYS[2], ARGV[1], token .. '|' .. ARGV[3] .. '|' .. nextstate .. '|' .. ARGV[5])
return 'updated'
'''

# Check the selected record, index membership and all required control fields in
# one server snapshot. Cardinality alone would miss an absent selected member.
VERIFY_LUA = r'''
local control = redis.call('GET', KEYS[4])
if not control or not string.match(control, '^ready|2|') then return 'UNKNOWN' end
if control ~= ARGV[4] then return 'UNKNOWN' end
for i=1,4 do if redis.call('PTTL', KEYS[i]) >= 0 then return 'UNKNOWN' end end
if redis.call('HGET', KEYS[1], '__schema__') ~= control then return 'UNKNOWN' end
local retired = redis.call('HGET', KEYS[1], '__retired__')
local ordinal = redis.call('HGET', KEYS[1], '__ordinal__')
if not retired or not ordinal or not string.match(retired, '^%d+$') or not string.match(ordinal, '^%d+$') then return 'UNKNOWN' end
if redis.call('HLEN', KEYS[1]) - 3 ~= redis.call('ZCARD', KEYS[2]) then return 'UNKNOWN' end
if redis.call('HLEN', KEYS[3]) ~= redis.call('ZCARD', KEYS[2]) then return 'UNKNOWN' end
if not redis.call('ZSCORE', KEYS[2], ARGV[1]) then return 'UNKNOWN' end
local raw = redis.call('HGET', KEYS[1], ARGV[1])
if not raw then return 'UNKNOWN' end
local reserved, committed, status, token = string.match(raw, '^(%d+)|(%d+)|([uc])|(.+)$')
if not reserved or reserved ~= committed or committed ~= ARGV[2] or status ~= 'c' or token ~= ARGV[3] then return 'UNKNOWN' end
local quality = redis.call('HGET', KEYS[3], ARGV[1])
if not quality then return 'UNKNOWN' end
local owner, serial, state = string.match(quality, '^([^|]+)|(%d+)|([^|]+)|')
if owner ~= token then return 'UNKNOWN' end
if state == 'confirmed' then return 'LIVE' end
if state == 'held' then return 'DEGRADED' end
if state == 'pending' or state == 'uncertain' then return 'UNACKNOWLEDGED' end
return 'UNKNOWN'
'''
