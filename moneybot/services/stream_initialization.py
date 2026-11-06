"""Explicit, bounded integrity cutover. Never invoked as a startup migration.

An owner-approved external manifest is an input, not proof fabricated by Redis.
Its storage, historical-authority and legacy-writer-fence evidence must be
independently verified before live use. No Redis snapshot can detect its own
consistent rollback. All mutation below requires that separate authorization.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from .stream_integrity import IntegrityRejection, MAX_WATERMARKS, Watermark


SNAPSHOT_LUA = r'''
if redis.call('HLEN', KEYS[2]) > 1027 or redis.call('ZCARD', KEYS[3]) > 1024
or redis.call('HLEN', KEYS[4]) > 1024 or redis.call('HLEN', KEYS[5]) > 1024 then
    return {'oversized'}
end
return {redis.call('GET', KEYS[1]) or '', redis.call('HGETALL', KEYS[2]),
redis.call('ZRANGE', KEYS[3], 0, -1, 'WITHSCORES'),
redis.call('HGETALL', KEYS[4]), redis.call('HGETALL', KEYS[5])}
'''

# Args carry complete expected and target snapshots, each strictly bounded.
# No transient schema-version keys accumulate. Incomplete control is durable
# before any structural edits, and readiness is the final effect. Reapplication
# uses the same external manifest; it never guesses missing historical bounds.
APPLY_LUA = r'''
local pos = 1
local function read()
    local value = ARGV[pos]; pos = pos + 1; return value
end
local function section()
    local size = tonumber(read()); local result = {}
    for i=1,size do local key = read(); local value = read(); result[key] = value end
    return result, size
end
local expected = read()
if (redis.call('GET', KEYS[1]) or '') ~= expected then return 'concurrent_change' end
for i=2,5 do
    local values, size = section()
    local current = i == 3 and redis.call('ZCARD', KEYS[i]) or redis.call('HLEN', KEYS[i])
    if current ~= size then return 'concurrent_change' end
    for key,value in pairs(values) do
        local found = i == 3 and redis.call('ZSCORE', KEYS[i], key) or redis.call('HGET', KEYS[i], key)
        if i == 3 then
            if not found or tonumber(found) ~= tonumber(value) then return 'concurrent_change' end
        elseif found ~= value then return 'concurrent_change' end
    end
end
local ready, pending = read(), read()
local hash = section(); local index = section(); local quality = section(); local contexts = section()
redis.call('SET', KEYS[1], pending)
-- Raising the retirement bound precedes every possible field removal.
redis.call('HSET', KEYS[2], '__retired__', hash['__retired__'])
for field,value in pairs(hash) do redis.call('HSET', KEYS[2], field, value) end
for _,field in ipairs(redis.call('HKEYS', KEYS[2])) do
    if not hash[field] then redis.call('HDEL', KEYS[2], field) end
end
redis.call('DEL', KEYS[3], KEYS[4], KEYS[5])
for field,value in pairs(index) do redis.call('ZADD', KEYS[3], value, field) end
for field,value in pairs(quality) do redis.call('HSET', KEYS[4], field, value) end
for field,value in pairs(contexts) do redis.call('HSET', KEYS[5], field, value) end
redis.call('SET', KEYS[1], ready)
return 'completed'
'''


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def keys(namespace):
    return [namespace + suffix for suffix in (':integrity-control', ':integrity', ':integrity-lru', ':integrity-quality', ':integrity-context')]


def snapshot(client, namespace):
    raw = client.eval(SNAPSHOT_LUA, 5, *keys(namespace))
    if not isinstance(raw, list) or len(raw) != 5:
        raise IntegrityRejection('oversized_or_invalid_integrity_state')
    result = {'control': raw[0]}
    for name, values in zip(('hash', 'index', 'quality', 'contexts'), raw[1:]):
        result[name] = dict(zip(values[::2], values[1::2]))
    return result


def validate_state(state, *, ready=True):
    h, index = state['hash'], state['index']
    if not h:
        return 'INCOMPLETE_METADATA' if any(state.values()) else 'INITIALIZATION_REQUIRED'
    if h.get('__schema__') == '1' and not state['control']:
        return 'INITIALIZATION_REQUIRED'
    expected_schema = state['control'].replace('incomplete|', 'ready|', 1)
    if h.get('__schema__') != expected_schema or not expected_schema.startswith('ready|2|'):
        return 'CORRUPT_METADATA'
    try:
        for name in ('__retired__', '__ordinal__'):
            value = h[name]
            if not re.fullmatch(r'\d+', value) or not 0 <= int(value) < 2**53:
                return 'CORRUPT_METADATA'
        fields = set(h) - {'__schema__', '__retired__', '__ordinal__'}
        if len(fields) > MAX_WATERMARKS:
            return 'CORRUPT_METADATA'
        if set(index) != fields:
            return 'INCOMPLETE_METADATA'
        for field in fields:
            if not re.fullmatch(r'(Q|T|A|AM):[A-Z0-9._-]{1,64}', field):
                return 'CORRUPT_METADATA'
            record = Watermark.decode(h[field])
            owner, revision = record.token.rsplit(':', 1)
            uuid.UUID(owner)
            if not revision.isdigit() or int(revision) <= 0 or record.reserved_us >= 2**53:
                return 'CORRUPT_METADATA'
            if record.status == 'c' and record.reserved_us != record.committed_us:
                return 'CORRUPT_METADATA'
            if not 0 < float(index[field]) <= int(h['__ordinal__']):
                return 'CORRUPT_METADATA'
        if ready:
            if not state['control'].startswith('ready|2|'):
                return 'INCOMPLETE_METADATA'
            if set(state['quality']) != fields or not set(state['contexts']) <= fields:
                return 'INCOMPLETE_METADATA'
            for field, quality in state['quality'].items():
                token, serial, status, reason = quality.split('|', 3)
                if token != Watermark.decode(h[field]).token or not serial.isdigit() or status not in {'pending', 'confirmed', 'held', 'uncertain'}:
                    return 'CORRUPT_METADATA'
            for field, encoded in state['contexts'].items():
                value = json.loads(encoded)
                record = Watermark.decode(h[field])
                if (not field.startswith(('A:', 'AM:')) or record.status != 'c'
                        or value.get('timestamp_us') != record.committed_us or value.get('token') != record.token
                        or type(value.get('sequence_number')) is not int):
                    return 'CORRUPT_METADATA'
    except (KeyError, ValueError, TypeError, IntegrityRejection):
        return 'CORRUPT_METADATA'
    return 'COMPATIBLE_VERIFIED_STATE'


def validate_authorization(authorization, namespace, now):
    if not isinstance(authorization, dict):
        raise IntegrityRejection('UNVERIFIED_PERSISTENCE')
    try:
        if authorization['schema'] != 2 or authorization['namespace'] != namespace:
            raise ValueError()
        uuid.UUID(authorization['generation'])
        expires = datetime.fromisoformat(authorization['expires_at'])
        if expires.tzinfo is None or not now < expires:
            raise ValueError()
        for name in ('durability_evidence_sha256', 'writer_fence_evidence_sha256', 'history_evidence_sha256'):
            if not re.fullmatch(r'[0-9a-f]{64}', authorization[name]):
                raise ValueError()
        if authorization['durability_contract'] != 'acknowledged_and_uncertain_bounds_survive':
            raise ValueError()
        if authorization['legacy_writers_fenced'] is not True:
            raise ValueError()
        if not authorization['server_run_id'] or not authorization['server_version']:
            raise ValueError()
        required_config = {'maxmemory-policy', 'appendonly', 'appendfsync', 'save', 'no-appendfsync-on-rewrite'}
        if set(authorization['configuration']) != required_config or not all(isinstance(v, str) for v in authorization['configuration'].values()):
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise IntegrityRejection('UNVERIFIED_PERSISTENCE_OR_WRITER_FENCE') from None
    return 'ready|2|' + authorization['generation'] + '|' + digest(authorization)


def storage_preflight(client, namespace, authorization, *, now=None):
    """Read-only. Evidence references require independent owner verification."""
    now = now or datetime.now(timezone.utc)
    ready = validate_authorization(authorization, namespace, now)
    policy = client.config_get('maxmemory-policy').get('maxmemory-policy')
    if policy != 'noeviction':
        raise IntegrityRejection('UNSUPPORTED_STORAGE_CONFIGURATION')
    observed = {'maxmemory-policy': policy}
    for name in sorted(set(authorization['configuration']) - {'maxmemory-policy'}):
        observed[name] = client.config_get(name).get(name)
    if observed != authorization['configuration']:
        raise IntegrityRejection('UNVERIFIED_PERSISTENCE_CONFIGURATION_CHANGED')
    server, persistence = client.info('server'), client.info('persistence')
    if server.get('run_id') != authorization['server_run_id'] or server.get('redis_version') != authorization['server_version']:
        raise IntegrityRejection('UNVERIFIED_SERVER_GENERATION')
    if persistence.get('loading') != 0 or persistence.get('aof_last_write_status', 'ok') != 'ok':
        raise IntegrityRejection('UNVERIFIED_PERSISTENCE')
    if client.info('replication').get('role') != 'master':
        raise IntegrityRejection('UNVERIFIED_WRITER_TOPOLOGY')
    # Actual no-loss/failover semantics cannot be inferred from INFO. The
    # independently approved evidence is mandatory even when CONFIG is safe.
    return ready


def preflight(client, namespace, authorization=None, *, now=None, required=None, known_contexts=()):
    try:
        state = snapshot(client, namespace)
        state_status = validate_state(state)
    except Exception as exc:
        corrupt = isinstance(exc, (IntegrityRejection, ValueError, TypeError)) or 'WRONGTYPE' in str(exc)
        return {'status': 'CORRUPT_METADATA' if corrupt else 'UNAVAILABLE_INTEGRITY_STORE', 'reason': type(exc).__name__}
    try:
        expected = storage_preflight(client, namespace, authorization, now=now)
    except Exception as exc:
        return {'status': str(exc) if isinstance(exc, IntegrityRejection) else 'UNVERIFIED_STORAGE_CONFIGURATION', 'state_status': state_status}
    if state_status != 'COMPATIBLE_VERIFIED_STATE':
        return {'status': state_status, 'state_status': state_status}
    if state['control'] != expected:
        return {'status': 'INCOMPATIBLE_GENERATION', 'state_status': state_status}
    if any(client.pttl(key) != -1 for key in keys(namespace) if client.exists(key)):
        return {'status': 'INCOMPLETE_METADATA', 'reason': 'authority_has_ttl'}
    for channel in ('A', 'AM'):
        for symbol in (required or {}).get(channel, ()):
            field = channel + ':' + symbol
            bound = Watermark.decode(state['hash'][field]).reserved_us if field in state['hash'] else int(state['hash']['__retired__'])
            if bound and field not in state['contexts'] and (channel, symbol) not in known_contexts:
                return {'status': 'AGGREGATE_CONTEXT_REQUIRED', 'field': field, 'reason': 'operator_authoritative_seed_required'}
    return {'status': 'COMPATIBLE_VERIFIED_STATE', 'generation': authorization['generation']}


def _pack_sections(state):
    result = []
    for name in ('hash', 'index', 'quality', 'contexts'):
        values = state[name]
        result.append(len(values))
        for key in sorted(values):
            result.extend((key, values[key]))
    return result


def reconcile(client, namespace, authorization, *, now=None):
    """Explicit owner-authorized operation; no provider calls or auto retry.

    The external manifest must contain complete target history and the exact
    approved pre-state digest. It is retained outside Redis for interrupted
    operation recovery. An absent hash never implies a fresh history epoch.
    """
    now = now or datetime.now(timezone.utc)
    ready = storage_preflight(client, namespace, authorization, now=now)
    pending = ready.replace('ready|', 'incomplete|', 1)
    current = snapshot(client, namespace)
    if any(client.pttl(key) != -1 for key in keys(namespace) if client.exists(key)):
        raise IntegrityRejection('authority_has_ttl_operator_required')
    if current['control'] == ready:
        if validate_state(current) != 'COMPATIBLE_VERIFIED_STATE':
            raise IntegrityRejection('completed_generation_requires_new_repair_manifest')
        return {'status': 'already_completed', 'completion_record': ready}
    if current['control'].split('|')[2:3] == [authorization['generation']] and current['control'] != pending:
        raise IntegrityRejection('generation_reuse_requires_distinct_repair_generation')
    if current['control'] != pending and digest(current) != authorization.get('expected_snapshot_sha256'):
        raise IntegrityRejection('unapproved_or_concurrent_state')
    target_hash = dict(authorization.get('target_hash', {}))
    target_hash['__schema__'] = ready
    # A manifest contains explicit historical authority. Never infer it from
    # latest prices; even an empty initial hash requires a justified retired B.
    fields = set(target_hash) - {'__schema__', '__retired__', '__ordinal__'}
    index = {field: str(i + 1) for i, field in enumerate(sorted(fields))}
    target_hash['__ordinal__'] = str(max(int(target_hash.get('__ordinal__', '0')), len(fields), int(current['hash'].get('__ordinal__', '0'))))
    quality = {field: Watermark.decode(target_hash[field]).token + '|0|pending|operator_reconciliation' for field in fields}
    target = {'control': ready, 'hash': target_hash, 'index': index, 'quality': quality,
              'contexts': dict(authorization.get('aggregate_contexts', {}))}
    if validate_state(target) != 'COMPATIBLE_VERIFIED_STATE':
        raise IntegrityRejection('invalid_target_manifest')
    floor = int(target_hash['__retired__'])
    if floor < int(current['hash'].get('__retired__', '0')):
        raise IntegrityRejection('retirement_floor_regression')
    for field, value in current['hash'].items():
        if field.startswith('__'):
            continue
        old = Watermark.decode(value)
        replacement = Watermark.decode(target_hash[field]) if field in target_hash else None
        if (replacement.reserved_us if replacement else floor) < old.reserved_us:
            raise IntegrityRejection('reserved_floor_regression')
        if replacement and replacement.committed_us < old.committed_us:
            raise IntegrityRejection('committed_floor_regression')
    for field, value in target['contexts'].items():
        record = Watermark.decode(target_hash[field])
        context = json.loads(value)
        if (not field.startswith(('A:', 'AM:')) or record.status != 'c'
                or context.get('timestamp_us') != record.committed_us or context.get('token') != record.token
                or type(context.get('sequence_number')) is not int
                or context.get('provider_event_id') is not None and not isinstance(context['provider_event_id'], str)):
            raise IntegrityRejection('unverified_aggregate_context')
    result = client.eval(APPLY_LUA, 5, *keys(namespace), current['control'], *_pack_sections(current), ready, pending, *_pack_sections(target))
    if result != 'completed':
        raise IntegrityRejection(str(result))
    observed = snapshot(client, namespace)
    # Concurrent new writers can only start after readiness; operator keeps
    # them fenced until this exact post-state verification is complete.
    if observed != target:
        raise IntegrityRejection('completion_verification_failed_operator_required')
    return {'status': 'completed', 'completion_record': ready, 'state_sha256': digest(observed)}
