from __future__ import annotations

import copy
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from moneybot.services import alpha_atlas_v4_stage_b_monitor as m
from moneybot.services.alpha_atlas_v4_prospective_snapshot import CaptureError, canonical_bytes, sha256_bytes

REPO = Path(__file__).resolve().parents[1]
START = datetime(2026, 10, 8, tzinfo=timezone.utc)
SHA = 'a' * 40
OUTPUT = Path('/var/data/moneybot-stage-b-monitoring/test-only')


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('Network forbidden in monitoring tests')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket.socket, 'connect_ex', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(socket, 'getaddrinfo', blocked)


def auth():
    return {'schema_version': m.AUTH_SCHEMA, 'status': 'APPROVED',
            'owner_approval': 'APPROVED_FOR_BOUNDED_PASSIVE_MONITORING',
            'authorization_id': 'test-only', 'purpose': 'PASSIVE_STREAM_RESOURCE_EVIDENCE_ONLY',
            'source_revision': SHA, 'target_worker': m.TARGET,
            'starts_at': START.isoformat(), 'ends_at': (START+timedelta(seconds=120)).isoformat(),
            'max_duration_seconds': 120, 'sampling_interval_seconds': 10, 'max_samples': 12,
            'storage_limit_bytes': m.MAX_STORAGE, 'output_dir': str(OUTPUT),
            'phases': [{'phase': p, 'starts_at': (START+timedelta(seconds=40*i)).isoformat(),
                        'ends_at': (START+timedelta(seconds=40*(i+1))).isoformat(), 'workload_marker_sha256': None}
                       for i, p in enumerate(m.PHASES)],
            'redis_read_scope': {'commands': ['GET'], 'key': m.HEALTH_KEY},
            'stop_conditions': m.STOPS,
            'source_binding': {'kind': 'OWNER_BOUND_HEALTH_KEY', 'worker_instance_id': 'synthetic-worker'},
            'resources': {'pid': None, 'start_ticks': None, 'boot_id': None,
                          'filesystem_mount': '/var/data', 'filesystem_device': 1}}


def sign(a):
    a = copy.deepcopy(a); a.pop('content_sha256', None)
    a['content_sha256'] = sha256_bytes(canonical_bytes(a))
    return a, sha256_bytes(canonical_bytes(a))


def run(tmp_path, *, health=None, resource=None, mutate=None, capacity=lambda: None):
    a = auth()
    if mutate:
        mutate(a)
    values = health if health is not None else m.synthetic_fixture()
    current = [START]; index = [0]
    def read():
        value = values[index[0]]; index[0] += 1
        if isinstance(value, BaseException):
            raise value
        return copy.deepcopy(value)
    def advance(seconds):
        current[0] += timedelta(seconds=seconds)
    return m.run_monitor(a, tmp_path/'evidence', mode='OFFLINE_SYNTHETIC', reader=read,
                         resources=resource or (lambda: m.synthetic_resources(m.registered_limits(REPO))),
                         clock=lambda: current[0], wait=advance, capacity_check=capacity)


def healthy():
    values = m.synthetic_fixture()
    first = values[0]
    for i in range(12):
        value = copy.deepcopy(first)
        value['updated_at'] = value['last_message_at'] = (START+timedelta(seconds=10*i)).isoformat()
        value['metrics']['messages_received']['T'] = i*100
        values[i] = value
    return values


def test_valid_health_and_three_phase_comparisons(tmp_path):
    result = run(tmp_path, health=healthy())
    assert result['status'] == 'COMPLETE_WITH_UNKNOWNS'
    assert result['observation_count'] == 12
    assert result['gaps'] == []
    assert result['phase_coverage'] == dict.fromkeys(m.PHASES, 4)
    for phase in m.PHASES:
        comparison = result['comparisons']['phases'][phase]
        assert comparison['messages_per_second'] == 10
        assert comparison['counter_changes']['reconnect_count'] == 0
        assert comparison['resource_bounds']['peak_rss_kib'] == 12000
    assert result['comparisons']['versus_baseline']['AFTER_TEST']['messages_per_second_difference'] == 0
    assert result['comparisons']['stream_interference_acceptance'] == 'UNKNOWN'
    assert result['overall_stage_b'] == 'OPEN'


@pytest.mark.parametrize('change,reason', [
    (lambda x: x.update(updated_at='bad'), 'INVALID_TIMESTAMP'),
    (lambda x: x.update(updated_at='2026-10-08T00:00:00'), 'INVALID_TIMESTAMP'),
    (lambda x: x.update(schema_version='wrong'), 'HEALTH_SCHEMA_INVALID'),
    (lambda x: x.pop('metrics'), 'HEALTH_MALFORMED'),
    (lambda x: x.update(worker_instance_id='other-worker'), 'WORKER_IDENTITY_MISMATCH'),
    (lambda x: x['metrics'].update(dropped_events=-1), 'INVALID_INTEGER'),
    (lambda x: x['metrics'].update(dropped_events=True), 'INVALID_INTEGER'),
    (lambda x: x['metrics'].update(messages_received={'T': 1.5}), 'INVALID_INTEGER'),
    (lambda x: x.update(updated_at='2026-10-09T00:00:00Z'), 'HEALTH_TIME_OUTSIDE_OBSERVATION'),
    (lambda x: x['metrics']['event_to_redis_lag_ms'].update(p50=float('nan')), 'HEALTH_LATENCY_INVALID'),
])
def test_malformed_or_mismatched_health_is_unknown(change, reason):
    value = healthy()[0]; change(value)
    result = m.health_observation(value, START, 'synthetic-worker')
    assert result['status'] == 'UNKNOWN' and result['snapshot'] is None
    assert reason in result['reasons']


def test_missing_stale_disconnected_and_identity_unknown():
    assert 'HEALTH_ABSENT' in m.health_observation(None, START, None)['reasons']
    value = healthy()[0]
    assert 'HEALTH_STALE' in m.health_observation(value, START+timedelta(seconds=31), 'synthetic-worker')['reasons']
    value['connection_state'] = 'reconnecting'
    assert 'STREAM_NOT_CONNECTED' in m.health_observation(value, START, 'synthetic-worker')['reasons']
    value.pop('worker_instance_id')
    result = m.health_observation(value, START, 'synthetic-worker')
    assert 'UNKNOWN_SOURCE_IDENTITY' in result['reasons']
    assert result['snapshot']['worker_instance_id'] is None


@pytest.mark.parametrize('identity_known', [True, False])
def test_restart_boundaries_do_not_produce_negative_deltas(tmp_path, identity_known):
    values = healthy()
    for i in range(6, 12):
        values[i]['connected_at'] = (START+timedelta(seconds=60)).isoformat()
        values[i]['metrics']['messages_received']['T'] = (i-6)*100
        if not identity_known:
            values[i].pop('worker_instance_id')
    result = run(tmp_path, health=values)
    assert result['comparisons']['boundaries'][0]['observation_index'] == 6
    for phase in result['comparisons']['phases'].values():
        assert phase['message_changes'] is None or all(x >= 0 for x in phase['message_changes'].values())


def test_counter_reset_without_restart_and_nonincreasing_timestamp(tmp_path):
    values = healthy()
    values[2]['metrics']['messages_received']['T'] = 50
    values[5]['updated_at'] = values[4]['updated_at']
    values[5]['last_message_at'] = values[4]['last_message_at']
    result = run(tmp_path, health=values)
    reasons = {b['reason'] for b in result['comparisons']['boundaries']}
    assert 'APPARENT_RESTART_OR_COUNTER_RESET' in reasons
    assert 'NONINCREASING_HEALTH_TIMESTAMP' in reasons


@pytest.mark.parametrize('missing', m.PHASES)
def test_missing_phase_is_never_invented(tmp_path, missing):
    result = run(tmp_path, health=healthy(), mutate=lambda a: a.update(phases=[p for p in a['phases'] if p['phase'] != missing]))
    assert missing in result['missing_phases']
    assert result['comparisons']['phases'][missing]['status'] == 'UNKNOWN'


def test_resource_isolation_and_wrong_filesystem_attribution(tmp_path, monkeypatch):
    monkeypatch.setattr(m.os, 'statvfs', lambda p: (_ for _ in ()).throw(OSError('not accessible')))
    binding = auth()['resources']; binding['pid'] = os.getpid()
    measured = m.local_resources(binding, m.registered_limits(REPO))
    assert measured['process_status'] == measured['filesystem_status'] == 'UNKNOWN'
    assert measured['process'] is None and measured['filesystem'] is None
    result = run(tmp_path, resource=lambda: measured, health=healthy())
    assert 'PROCESS_LEVEL_RESOURCE_COMPLIANCE' in result['remaining_unknown']
    assert all(x['status'] == 'UNKNOWN' for x in result['comparisons']['phases'].values())


def test_filesystem_path_does_not_establish_shared_mount(monkeypatch):
    class Stats:
        f_bavail = 100; f_frsize = 4096
    original = Path.read_text
    def read(path, *args, **kwargs):
        if str(path) == '/proc/self/mountinfo':
            return '1 0 0:1 / / rw - overlay overlay rw\n'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    monkeypatch.setattr(Path, 'stat', lambda *a, **k: type('Stat', (), {'st_dev': 999})())
    monkeypatch.setattr(m.os, 'statvfs', lambda p: Stats())
    measured = m.local_resources(auth()['resources'], {'minimum_free_bytes': m.COMBINED_CAP})
    assert measured['filesystem']['owner_binding_matches'] is False
    assert measured['filesystem']['measurement_class'] == 'OBSERVER_MOUNT_NAMESPACE'
    assert measured['filesystem_status'] == 'UNKNOWN'


def test_authorization_exact_binding_and_scope():
    a, pin = sign(auth())
    assert m.validate_authorization(a, pin, now=START, source_revision=SHA, output=OUTPUT) == a
    with pytest.raises(CaptureError, match='HASH_MISMATCH'):
        m.validate_authorization(a, '0'*64, now=START, source_revision=SHA, output=OUTPUT)


@pytest.mark.parametrize('change', [
    lambda a: a.update(status='PREPARED'),
    lambda a: a.update(schema_version='acquisition-authorization'),
    lambda a: a.update(source_revision='b'*40),
    lambda a: a.update(target_worker='different'),
    lambda a: a.update(redis_read_scope={'commands': ['GET', 'SCAN'], 'key': m.HEALTH_KEY}),
    lambda a: a.update(max_duration_seconds=3301),
    lambda a: a.update(max_samples=361),
    lambda a: a.update(max_samples=1),
    lambda a: a.update(sampling_interval_seconds=1),
    lambda a: a.update(storage_limit_bytes=3*m.MAX_STORAGE),
    lambda a: a.update(ends_at=START.isoformat()),
    lambda a: a.update(starts_at=(START+timedelta(seconds=1)).isoformat()),
    lambda a: a.update(stop_conditions=[]),
    lambda a: a.update(secret='must-not-be-accepted'),
    lambda a: a['phases'][0].update(ends_at=(START+timedelta(seconds=60)).isoformat()),
    lambda a: a['resources'].update(pid=123),
])
def test_invalid_authorization_cannot_enable_passive_read(change):
    a = auth(); change(a); a, pin = sign(a)
    with pytest.raises(CaptureError):
        m.validate_authorization(a, pin, now=START, source_revision=SHA, output=OUTPUT)


@pytest.mark.parametrize('path', ['/var/data/moneybot-stage-b/primary/new', '/var/data/moneybot-stage-b/isolated-restore/new',
                                  '/var/data/moneybot-stage-b/s3-operation-ledger/new', '/var/data/moneybot-stage-b/backup/new'])
def test_forbidden_output_paths(path):
    a = auth(); a['output_dir'] = path; a, pin = sign(a)
    with pytest.raises(CaptureError, match='OUTPUT_SCOPE'):
        m.validate_authorization(a, pin, now=START, source_revision=SHA, output=Path(path))


def test_invalid_passive_authorization_precedes_redis_and_environment(tmp_path, monkeypatch):
    def forbidden(*args):
        raise AssertionError('must not construct connection')
    monkeypatch.setattr(m, 'RedisHealthReader', forbidden)
    monkeypatch.setattr(m, 'revision', lambda _: SHA)
    auth_path = tmp_path/'auth.json'; auth_path.write_text('{}')
    with pytest.raises(CaptureError):
        m.observe_passive(REPO, auth_path, '0'*64, tmp_path/'out', clock=lambda: START)
    assert not (tmp_path/'out').exists()


def test_no_clobber_and_synthetic_runtime_paths(tmp_path):
    output = tmp_path/'existing'; output.mkdir(); (output/'keep').write_text('immutable')
    with pytest.raises(CaptureError, match='OUTPUT_EXISTS'):
        m.run_synthetic(REPO, output)
    assert (output/'keep').read_text() == 'immutable'
    with pytest.raises(CaptureError, match='RUNTIME_PATH_FORBIDDEN'):
        m.run_synthetic(REPO, Path('/var/data/moneybot-stage-b/new'))


@pytest.mark.parametrize('limit,code', [(1, 'MONITOR_SAMPLE_LIMIT'), (m.FINAL_RESERVE+100, 'MONITOR_STORAGE_LIMIT')])
def test_bounded_storage_and_sample_limit(tmp_path, limit, code):
    def mutate(a):
        a['max_samples' if code == 'MONITOR_SAMPLE_LIMIT' else 'storage_limit_bytes'] = limit
    result = run(tmp_path, mutate=mutate)
    assert result['status'] == 'INCOMPLETE' and result['stop_reason'] == code
    assert result['storage_bytes_consumed'] <= (m.MAX_STORAGE if code == 'MONITOR_SAMPLE_LIMIT' else limit)


def test_capacity_failure_and_interrupt_are_incomplete(tmp_path):
    def capacity():
        raise CaptureError('MONITOR_CAPACITY_EXHAUSTED')
    result = run(tmp_path, capacity=capacity)
    assert result['status'] == 'INCOMPLETE' and result['observation_count'] == 0
    second = tmp_path/'second'; second.mkdir()
    result = run(second, health=[healthy()[0], KeyboardInterrupt()])
    assert result['status'] == 'INCOMPLETE' and result['stop_reason'] == 'INTERRUPTED'
    assert result['observation_count'] == 1


def test_integrity_manifest_tampering_and_abrupt_incomplete(tmp_path):
    output = tmp_path/'example'
    result = m.run_synthetic(REPO, output)
    assert m.verify_evidence(output) == result
    manifest = (output/'SHA256SUMS').read_text()
    assert '/var/data' not in manifest and '../' not in manifest
    (output/'observations.jsonl').write_bytes((output/'observations.jsonl').read_bytes()+b'\n')
    with pytest.raises(CaptureError, match='INTEGRITY'):
        m.verify_evidence(output)
    writer = m.EvidenceWriter(tmp_path/'abrupt', m.MAX_STORAGE)
    writer.stream.close()  # Simulate termination before finalization.
    with pytest.raises(CaptureError, match='INCOMPLETE'):
        m.verify_evidence(tmp_path/'abrupt')


def test_secret_fields_and_exception_text_never_archived(tmp_path):
    values = healthy(); secret = 'redis://username:SECRET_PASSWORD@example.invalid:6379'
    for value in values:
        value.update(last_error=secret, websocket_url=secret, api_key=secret)
        value['metrics']['raw_payload'] = {'secret': secret}
    values[2] = RuntimeError(secret)
    result = run(tmp_path, health=values)
    assert result['status'] == 'INCOMPLETE'
    assert result['stop_reason'] == 'OBSERVATION_FAILED'
    for path in (tmp_path/'evidence').iterdir():
        assert secret.encode() not in path.read_bytes()


def test_synthetic_deterministic_and_no_credentials_or_provider_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'RedisHealthReader', lambda *_: (_ for _ in ()).throw(AssertionError('Redis forbidden')))
    monkeypatch.setattr(m, 'local_resources', lambda *_: (_ for _ in ()).throw(AssertionError('runtime forbidden')))
    for name in ('REDIS_URL', 'MASSIVE_API_KEY', 'MONEYBOT_STAGE_B_AWS_ACCESS_KEY_ID'):
        monkeypatch.setenv(name, 'MUST_NOT_READ')
    a, b = tmp_path/'a', tmp_path/'b'
    m.run_synthetic(REPO, a); m.run_synthetic(REPO, b)
    assert {p.name: p.read_bytes() for p in a.iterdir()} == {p.name: p.read_bytes() for p in b.iterdir()}
    assert len(list(a.iterdir())) == 4


def test_cli_is_manual_and_missing_live_authorization_is_blocked(tmp_path):
    result = subprocess.run([sys.executable, '-m', 'scripts.run_alpha_atlas_v4_stage_b_monitor',
                             '--passive-observation', '--output-dir', str(tmp_path/'out')],
                            cwd=REPO, capture_output=True, text=True)
    assert result.returncode == 2 and 'BLOCKED_OR_INCOMPLETE' in result.stdout
    assert not (tmp_path/'out').exists()
    result = subprocess.run([sys.executable, '-m', 'scripts.run_alpha_atlas_v4_stage_b_monitor',
                             '--output-dir', str(tmp_path/'out')], cwd=REPO, capture_output=True, text=True)
    assert result.returncode != 0


def test_read_only_adapter_surface_and_routing_isolation():
    source = (REPO/'moneybot/services/alpha_atlas_v4_stage_b_monitor.py').read_text()
    assert 'self.client.get(HEALTH_KEY)' in source
    for expression in ('self.client.set(', 'self.client.scan(', 'self.client.publish(', 'self.client.subscribe(',
                       'boto3', 'run_market_stream', 'TrackB', 'V3.1'):
        assert expression not in source
    assert not any('monitor' in p.read_text() for p in [REPO/'scripts/run_market_stream.py'])


def test_adapter_only_get_with_retries_disabled(monkeypatch):
    import redis
    reads, config = [], {}
    class Client:
        def get(self, key):
            reads.append(key)
            return canonical_bytes(healthy()[0])
        def close(self):
            pass
    def create(url, **kwargs):
        config.update(kwargs)
        return Client()
    monkeypatch.setattr(redis.Redis, 'from_url', create)
    reader = m.RedisHealthReader('redis://test-only.invalid')
    assert reader()['schema_version'] == m.STREAM_SCHEMA_VERSION
    assert reads == [m.HEALTH_KEY]
    assert config['socket_timeout'] == config['socket_connect_timeout'] == 2
    assert config['retry_on_timeout'] is False
    assert config['health_check_interval'] == 0
    assert config['lib_name'] is None and config['lib_version'] is None
    reader.close()


def test_adapter_errors_are_redacted_and_snapshot_size_is_bounded(monkeypatch):
    import redis
    class Client:
        def get(self, key):
            return b'x' * (m.MAX_SNAPSHOT+1)
    monkeypatch.setattr(redis.Redis, 'from_url', lambda *a, **kw: Client())
    with pytest.raises(CaptureError, match='HEALTH_READ_ERROR'):
        m.RedisHealthReader('redis://password@example.invalid')()


def test_clock_regression_is_incomplete(tmp_path):
    current = [START]
    def wait(seconds):
        current[0] = START-timedelta(seconds=1)
    result = m.run_monitor(auth(), tmp_path/'clock', mode='OFFLINE_SYNTHETIC',
                           reader=lambda: healthy()[0], resources=lambda: m.synthetic_resources(m.registered_limits(REPO)),
                           clock=lambda: current[0], wait=wait)
    assert result['status'] == 'INCOMPLETE'
    assert result['stop_reason'] == 'MONITOR_CLOCK_REGRESSION'


def test_dirty_source_revision_cannot_authorize_passive_mode(tmp_path):
    # Build a tiny Git checkout; changed source cannot hide behind the same HEAD SHA.
    (tmp_path/'moneybot/services').mkdir(parents=True)
    (tmp_path/'scripts').mkdir()
    module = tmp_path/'moneybot/services/alpha_atlas_v4_stage_b_monitor.py'
    module.write_text('# approved source\n')
    (tmp_path/'scripts/run_alpha_atlas_v4_stage_b_monitor.py').write_text('# approved cli\n')
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(tmp_path), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '-qm', 'source'], check=True)
    assert len(m.revision(tmp_path)) == 40
    module.write_text('# modified source\n')
    with pytest.raises(CaptureError, match='DIRTY_SOURCE'):
        m.revision(tmp_path)


def test_schema_validation_rejects_wrong_version_even_with_matching_checksums(tmp_path):
    output = tmp_path/'schema'; m.run_synthetic(REPO, output)
    path = output/'observations.jsonl'
    path.write_bytes(path.read_bytes().replace(m.VERSION.encode(), m.VERSION.replace('.v1', '.v9').encode(), 1))
    lines = (output/'SHA256SUMS').read_text().splitlines()
    lines[0] = f'{sha256_bytes(path.read_bytes())}  observations.jsonl'
    (output/'SHA256SUMS').write_text('\n'.join(lines)+'\n')
    with pytest.raises(CaptureError, match='INTEGRITY'):
        m.verify_evidence(output)
