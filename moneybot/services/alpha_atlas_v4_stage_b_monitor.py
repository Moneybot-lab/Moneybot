"""Manual, bounded Stage B evidence observer; no provider or AWS client path.

The sole optional network adapter is lazy and restricted to one Redis GET key.
Missing attribution and measurements remain UNKNOWN; this is not an acceptance gate.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlsplit

from .alpha_atlas_v4_prospective_snapshot import CaptureError, canonical_bytes, sha256_bytes
from .alpha_atlas_v4_stage_b import COMBINED_CAP, REQUIRED_ROOT
from .market_stream import STREAM_SCHEMA_VERSION

UTC = timezone.utc
VERSION = 'alpha-atlas-v4-stage-b-monitor.v1'
AUTH_SCHEMA = 'alpha-atlas-v4-stage-b-monitor-authorization.v1'
HEALTH_KEY = 'moneybot:market:v1:health'
TARGET = 'moneybot-market-stream'
MONITOR_ROOT = Path('/var/data/moneybot-stage-b-monitoring')
PHASES = ('BASELINE_BEFORE', 'DURING_AUTHORIZED_TEST', 'AFTER_TEST')
MAX_DURATION = 3300
MAX_SAMPLES = 360
MAX_STORAGE = 2 * 1024 * 1024
FINAL_RESERVE = 128 * 1024
MAX_SNAPSHOT = 32 * 1024
COUNTERS = ('reconnect_count', 'dropped_events', 'sequence_gaps', 'parse_failures', 'slow_consumer_events')
LATENCIES = ('event_to_redis_lag_ms', 'redis_write_latency_ms')
STOPS = ['AUTHORIZATION_EXPIRATION', 'CAPACITY_EXHAUSTION', 'INTEGRITY_FAILURE', 'SAMPLE_LIMIT', 'READ_ERROR']


def stamp(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise CaptureError('INVALID_TIMESTAMP')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(UTC)
    except ValueError:
        raise CaptureError('INVALID_TIMESTAMP') from None


def integer(value: Any, *, minimum: int = 0, maximum: int = 2**63-1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise CaptureError('INVALID_INTEGER')
    return value


def label(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', value):
        raise CaptureError('INVALID_IDENTITY')
    return value


def revision(repo: Path) -> str:
    # A matching HEAD cannot authorize locally edited monitoring/stream/guard code.
    result = subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', 'moneybot', 'scripts',
                             'docs/reports/alpha_atlas_v4_stage_b_runtime_config.owner-bound.v1.json',
                             ':(exclude)**/__pycache__/**', ':(exclude)**/*.pyc'], cwd=repo)
    if result.returncode:
        raise CaptureError('MONITOR_DIRTY_SOURCE_REVISION')
    for path in ('moneybot/services/alpha_atlas_v4_stage_b_monitor.py',
                 'scripts/run_alpha_atlas_v4_stage_b_monitor.py'):
        if subprocess.run(['git', 'ls-files', '--error-unmatch', path], cwd=repo,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            raise CaptureError('MONITOR_DIRTY_SOURCE_REVISION')
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()


def registered_limits(repo: Path) -> dict[str, int]:
    """Reuse the committed guard configuration; no credentials/config environment reads."""
    config = json.loads((repo / 'docs/reports/alpha_atlas_v4_stage_b_runtime_config.owner-bound.v1.json').read_text())
    return {'max_rss_kib': integer(config['max_rss_kib'], minimum=1), 'minimum_free_bytes': COMBINED_CAP}


def validate_authorization(auth: Mapping[str, Any], pin: str, *, now: datetime,
                           source_revision: str, output: Path) -> dict[str, Any]:
    """All local gates run before connection configuration, Redis imports or clients."""
    try:
        body = dict(auth)
        digest = body.pop('content_sha256')
        if digest != sha256_bytes(canonical_bytes(body)) or pin != sha256_bytes(canonical_bytes(auth)):
            raise CaptureError('MONITOR_AUTH_HASH_MISMATCH')
        required = {'schema_version', 'status', 'owner_approval', 'authorization_id', 'purpose',
                    'source_revision', 'target_worker', 'starts_at', 'ends_at', 'max_duration_seconds',
                    'sampling_interval_seconds', 'max_samples', 'storage_limit_bytes', 'output_dir',
                    'phases', 'redis_read_scope', 'stop_conditions', 'source_binding', 'resources'}
        if set(body) != required:
            raise CaptureError('MONITOR_AUTH_FIELDS_INVALID')
        if (body['schema_version'] != AUTH_SCHEMA or body['status'] != 'APPROVED' or
                body['owner_approval'] != 'APPROVED_FOR_BOUNDED_PASSIVE_MONITORING' or
                body['purpose'] != 'PASSIVE_STREAM_RESOURCE_EVIDENCE_ONLY'):
            raise CaptureError('MONITOR_NOT_AUTHORIZED')
        label(body['authorization_id'])
        if not re.fullmatch('[0-9a-f]{40}', source_revision) or body['source_revision'] != source_revision:
            raise CaptureError('MONITOR_REVISION_MISMATCH')
        if body['target_worker'] != TARGET or body['redis_read_scope'] != {'commands': ['GET'], 'key': HEALTH_KEY}:
            raise CaptureError('MONITOR_SOURCE_SCOPE_INVALID')
        if body['stop_conditions'] != STOPS:
            raise CaptureError('MONITOR_STOP_SCOPE_INVALID')
        start, end = stamp(body['starts_at']), stamp(body['ends_at'])
        duration = integer(body['max_duration_seconds'], minimum=1, maximum=MAX_DURATION)
        interval = integer(body['sampling_interval_seconds'], minimum=5, maximum=60)
        samples = integer(body['max_samples'], minimum=1, maximum=MAX_SAMPLES)
        storage = integer(body['storage_limit_bytes'], minimum=FINAL_RESERVE+8192, maximum=MAX_STORAGE)
        if not start <= now < end or not 0 < (end-start).total_seconds() <= duration:
            raise CaptureError('MONITOR_OUTSIDE_WINDOW')
        if math.ceil((end-start).total_seconds()/interval) > samples:
            raise CaptureError('MONITOR_SAMPLE_SCOPE_INVALID')
        resolved = output.resolve()
        if resolved != Path(body['output_dir']).resolve() or not resolved.is_relative_to(MONITOR_ROOT):
            raise CaptureError('MONITOR_OUTPUT_SCOPE_INVALID')
        if resolved == MONITOR_ROOT or resolved.is_relative_to(Path(REQUIRED_ROOT)):
            raise CaptureError('MONITOR_OUTPUT_SCOPE_INVALID')
        binding = body['source_binding']
        if set(binding) != {'kind', 'worker_instance_id'} or binding['kind'] != 'OWNER_BOUND_HEALTH_KEY':
            raise CaptureError('MONITOR_SOURCE_SCOPE_INVALID')
        if binding['worker_instance_id'] is not None:
            label(binding['worker_instance_id'])
        resources = body['resources']
        if set(resources) != {'pid', 'start_ticks', 'boot_id', 'filesystem_mount', 'filesystem_device'}:
            raise CaptureError('MONITOR_RESOURCE_SCOPE_INVALID')
        if resources['pid'] is not None:
            integer(resources['pid'], minimum=1)
            integer(resources['start_ticks'], minimum=1)
            if not re.fullmatch(r'[0-9a-f-]{36}', str(resources['boot_id'])):
                raise CaptureError('MONITOR_RESOURCE_SCOPE_INVALID')
        elif resources['start_ticks'] is not None or resources['boot_id'] is not None:
            raise CaptureError('MONITOR_RESOURCE_SCOPE_INVALID')
        if resources['filesystem_mount'] != '/var/data':
            raise CaptureError('MONITOR_RESOURCE_SCOPE_INVALID')
        if resources['filesystem_device'] is not None:
            integer(resources['filesystem_device'])
        phase_end = start
        if not isinstance(body['phases'], list) or not 1 <= len(body['phases']) <= 3:
            raise CaptureError('MONITOR_PHASE_SCOPE_INVALID')
        order = []
        for phase in body['phases']:
            if set(phase) != {'phase', 'starts_at', 'ends_at', 'workload_marker_sha256'} or phase['phase'] not in PHASES:
                raise CaptureError('MONITOR_PHASE_SCOPE_INVALID')
            a, b = stamp(phase['starts_at']), stamp(phase['ends_at'])
            if not start <= a < b <= end or a < phase_end:
                raise CaptureError('MONITOR_PHASE_SCOPE_INVALID')
            marker = phase['workload_marker_sha256']
            if marker is not None and not re.fullmatch('[0-9a-f]{64}', str(marker)):
                raise CaptureError('MONITOR_PHASE_SCOPE_INVALID')
            phase_end = b
            order.append(PHASES.index(phase['phase']))
        if order != sorted(set(order)):
            raise CaptureError('MONITOR_PHASE_SCOPE_INVALID')
        return dict(auth)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise CaptureError('MONITOR_AUTH_INVALID') from None


def redis_connection_options(url: str) -> dict[str, Any]:
    """Validate URL options before any client/pool construction; never echo secrets.

    redis-py URL queries override from_url kwargs, including unknown connection
    options. Only auth in userinfo, database and verified TLS configuration are
    supported here. All operational options are imposed after URL parsing.
    """
    from redis.connection import parse_url
    from redis.retry import Retry
    from redis.backoff import NoBackoff

    tls_options = {'ssl_ca_certs', 'ssl_ca_path', 'ssl_ca_data', 'ssl_certfile',
                   'ssl_keyfile', 'ssl_password', 'ssl_cert_reqs', 'ssl_check_hostname'}
    try:
        if (not isinstance(url, str) or len(url) > 16384 or
                any(ord(char) < 32 or ord(char) == 127 for char in url)):
            raise ValueError()
        parts = urlsplit(url)
        if parts.scheme not in ('redis', 'rediss') or not parts.hostname or parts.fragment:
            raise ValueError()
        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
        names = [key for key, _ in pairs]
        # Unknown, duplicate and blank options are rejected, even equal-to-default
        # operational values. No path can forward hidden options to redis-py.
        if len(names) != len(set(names)) or set(names)-({'db'} | tls_options):
            raise ValueError()
        if any(not value for _, value in pairs):
            raise ValueError()
        if parts.scheme != 'rediss' and set(names) & tls_options:
            raise ValueError()
        if parts.path not in ('', '/') and not re.fullmatch(r'/[0-9]+', parts.path):
            raise ValueError()
        options = parse_url(url)
        path_db = int(parts.path[1:]) if parts.path not in ('', '/') else None
        db = integer(options.get('db', 0), maximum=2**31-1)
        if path_db is not None and path_db != db:
            raise ValueError()
        if options.get('ssl_cert_reqs', 'required') != 'required' or options.get('ssl_check_hostname', True) is not True:
            raise ValueError()
        if bool(options.get('ssl_certfile')) != bool(options.get('ssl_keyfile')):
            raise ValueError()
        allowed = {'host', 'port', 'username', 'password', 'db', 'connection_class'} | tls_options
        if set(options)-allowed:
            raise ValueError()
        options['db'] = db
        if parts.scheme == 'rediss':
            options.update(ssl_cert_reqs='required', ssl_check_hostname=True)
        # Build a pool explicitly; from_url is never given the untrusted URL.
        options.update(decode_responses=False, socket_timeout=2, socket_connect_timeout=2,
                       socket_keepalive=False, retry_on_timeout=False, retry_on_error=[],
                       retry=Retry(NoBackoff(), 0), health_check_interval=0, protocol=2,
                       client_name=None, lib_name=None, lib_version=None, max_connections=1)
        return options
    except (ValueError, TypeError, CaptureError):
        raise CaptureError('MONITOR_REDIS_CONFIGURATION_INVALID') from None


class RedisHealthReader:
    """Only GET of the existing key; no scans, health writes, reconnect loops or retries."""
    def __init__(self, url: str):
        options = redis_connection_options(url)  # Reject before constructing client/pool.
        import redis  # Lazy, only after live authorization gates.
        pool = redis.ConnectionPool(**options)
        self.pool = pool
        self.client = redis.Redis(connection_pool=pool)

    def __call__(self) -> dict[str, Any] | None:
        try:
            raw = self.client.get(HEALTH_KEY)
            if raw is None:
                return None
            if len(raw) > MAX_SNAPSHOT:
                raise CaptureError('HEALTH_SIZE_LIMIT')
            return json.loads(raw)
        except Exception:
            raise CaptureError('HEALTH_READ_ERROR') from None

    def close(self) -> None:
        try:
            self.client.close()
        finally:
            self.pool.disconnect()


def health_observation(raw: Any, observed: datetime, expected_instance: str | None) -> dict[str, Any]:
    """Whitelist only metrics; never archive raw health payload, URLs, symbols or errors."""
    result: dict[str, Any] = {'status': 'UNKNOWN', 'reasons': [], 'snapshot': None}
    if raw is None or raw == {}:
        result['reasons'] = ['HEALTH_ABSENT', 'UNKNOWN_SOURCE_IDENTITY']
        return result
    try:
        if not isinstance(raw, dict) or raw.get('schema_version') != STREAM_SCHEMA_VERSION:
            raise CaptureError('HEALTH_SCHEMA_INVALID')
        updated, connected, last = (stamp(raw[x]) for x in ('updated_at', 'connected_at', 'last_message_at'))
        if any(t > observed for t in (updated, connected, last)) or connected > updated or last > updated:
            raise CaptureError('HEALTH_TIME_OUTSIDE_OBSERVATION')
        age = (observed-updated).total_seconds()
        if age > 30:
            result['reasons'].append('HEALTH_STALE')
        state = raw['connection_state']
        if state not in ('connected', 'authenticated', 'connecting', 'disconnected', 'reconnecting', 'disabled', 'starting', 'authenticating', 'idle_no_demand'):
            raise CaptureError('HEALTH_STATE_INVALID')
        if state != 'connected':
            result['reasons'].append('STREAM_NOT_CONNECTED')
        identity = raw.get('worker_instance_id')
        if identity is not None:
            identity = label(identity)
        if identity is None or expected_instance is None:
            result['reasons'].append('UNKNOWN_SOURCE_IDENTITY')
        elif identity != expected_instance:
            raise CaptureError('WORKER_IDENTITY_MISMATCH')
        metrics = raw['metrics']
        counters = {key: integer(metrics[key]) for key in COUNTERS}
        messages = metrics['messages_received']
        if not isinstance(messages, dict) or len(messages) > 4 or set(messages)-{'A', 'AM', 'T', 'Q'}:
            raise CaptureError('HEALTH_COUNTER_INVALID')
        counts = {key: integer(value) for key, value in messages.items()}
        latencies = {}
        for key in LATENCIES:
            values = metrics[key]
            if not isinstance(values, dict) or set(values) != {'p50', 'p95', 'p99'}:
                raise CaptureError('HEALTH_LATENCY_INVALID')
            for value in values.values():
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                    raise CaptureError('HEALTH_LATENCY_INVALID')
            if any(value is None for value in values.values()):
                result['reasons'].append('LATENCY_UNAVAILABLE')
            else:
                if not values['p50'] <= values['p95'] <= values['p99']:
                    raise CaptureError('HEALTH_LATENCY_INVALID')
            latencies[key] = dict(values)
        result['snapshot'] = {'schema_version': STREAM_SCHEMA_VERSION, 'updated_at': updated.isoformat(),
                              'connected_at': connected.isoformat(), 'last_message_at': last.isoformat(),
                              'connection_state': state, 'worker_instance_id': identity,
                              'health_age_seconds': age, 'messages_received': counts, **counters, **latencies}
        result['status'] = 'VALID' if not result['reasons'] else 'UNKNOWN'
    except (CaptureError, KeyError, TypeError, ValueError) as exc:
        result['reasons'] = [exc.code if isinstance(exc, CaptureError) else 'HEALTH_MALFORMED', 'UNKNOWN_SOURCE_IDENTITY']
        result['snapshot'] = None
    return result


def local_resources(binding: Mapping[str, Any], limits: Mapping[str, int]) -> dict[str, Any]:
    """Linux procfs selected PID only; no environment, command line or secret access.

    PID mapping to the stream remains owner-declared. Proc start ticks/boot identity
    guard PID reuse. Filesystem attribution is separate from a matching path string.
    """
    out: dict[str, Any] = {'process_status': 'UNKNOWN', 'filesystem_status': 'UNKNOWN',
                           'source': 'LINUX_PROCFS_AND_STATVFS', 'attribution': 'OWNER_DECLARED_UNVERIFIED',
                           'limits': dict(limits), 'process': None, 'filesystem': None}
    pid = binding['pid']
    if pid is not None and pid != os.getpid():
        try:
            boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            text = Path(f'/proc/{pid}/stat').read_text()
            parts = text[text.rfind(')')+2:].split()
            start_ticks = int(parts[19])
            if boot != binding['boot_id'] or start_ticks != binding['start_ticks']:
                raise ValueError()
            status = Path(f'/proc/{pid}/status').read_text().splitlines()
            fields = {line.split(':', 1)[0]: line.split(':', 1)[1].split() for line in status if ':' in line}
            rss, peak = int(fields['VmRSS'][0]), int(fields['VmHWM'][0])
            if fields['VmRSS'][1] != 'kB' or fields['VmHWM'][1] != 'kB':
                raise ValueError()
            hz = os.sysconf('SC_CLK_TCK')
            boot_seconds = next(int(line.split()[1]) for line in Path('/proc/stat').read_text().splitlines() if line.startswith('btime '))
            out['process'] = {'pid': pid, 'start_ticks': start_ticks, 'boot_id': boot,
                              'started_at': datetime.fromtimestamp(boot_seconds+start_ticks/hz, UTC).isoformat(),
                              'rss_kib': rss, 'peak_rss_kib': peak,
                              'cpu_seconds': (int(parts[11])+int(parts[12]))/hz,
                              'measurement_class': 'SELECTED_PID_OWNER_MAPPING_UNVERIFIED'}
            # Measurement is valid for selected process, not conclusive market-stream attribution.
        except (OSError, KeyError, ValueError, IndexError, StopIteration):
            pass
    try:
        root = Path(REQUIRED_ROOT)
        stats = os.statvfs(root)
        dev = root.stat().st_dev
        mounts = []
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            fields = line.split()
            mount = Path(fields[4])
            if root.is_relative_to(mount):
                mounts.append((len(str(mount)), mount, fields[2]))
        _, mount, device = max(mounts)
        matches = str(mount) == binding['filesystem_mount'] and dev == binding['filesystem_device']
        out['filesystem'] = {'root': REQUIRED_ROOT, 'mount': str(mount), 'device': device,
                             'st_dev': dev, 'free_bytes': stats.f_bavail*stats.f_frsize,
                             'measurement_class': 'OBSERVER_MOUNT_NAMESPACE',
                             'owner_binding_matches': matches}
        # Even matching owner binding cannot prove another namespace shares this mount.
    except (OSError, ValueError, IndexError):
        pass
    return out


def synthetic_resources(limits: Mapping[str, int]) -> dict[str, Any]:
    return {'process_status': 'SYNTHETIC', 'filesystem_status': 'SYNTHETIC',
            'source': 'DETERMINISTIC_FIXTURE', 'attribution': 'SYNTHETIC_ONLY', 'limits': dict(limits),
            'process': {'pid': 100, 'start_ticks': 1, 'started_at': '2026-10-08T00:00:00+00:00',
                        'rss_kib': 10000, 'peak_rss_kib': 12000, 'cpu_seconds': 1.0},
            'filesystem': {'root': 'SYNTHETIC_ROOT', 'mount': 'SYNTHETIC_MOUNT', 'st_dev': 1,
                           'free_bytes': 100_000_000, 'owner_binding_matches': True}}


def phase_at(auth: Mapping[str, Any], now: datetime) -> dict[str, Any] | None:
    return next((p for p in auth['phases'] if stamp(p['starts_at']) <= now < stamp(p['ends_at'])), None)


class EvidenceWriter:
    """Incremental fsynced observations; SHA256SUMS is the final atomic completion marker.

    SIGKILL/power loss leaves .incomplete and/or lacks the completion manifest.
    Consumer must verify all four files, manifest and summary status before review.
    """
    def __init__(self, output: Path, limit: int):
        self.output, self.limit, self.used = output, limit, 0
        if output.exists():
            raise CaptureError('MONITOR_OUTPUT_EXISTS')
        output.mkdir(parents=True, exist_ok=False)
        self._atomic('.incomplete', b'INCOMPLETE: no final manifest; no acceptance certification\n')
        self.stream = (output/'observations.jsonl').open('xb')
        self.digest = hashlib.sha256()
        self.count = 0

    def _atomic(self, name: str, data: bytes) -> None:
        if self.used + len(data) > self.limit:
            raise CaptureError('MONITOR_STORAGE_LIMIT')
        temp = self.output/(name+'.tmp')
        with temp.open('xb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, self.output/name)
        self.used += len(data)

    def append(self, observation: Mapping[str, Any]) -> None:
        data = canonical_bytes(observation)
        if len(data) > 8192 or self.used + len(data) > self.limit-FINAL_RESERVE:
            raise CaptureError('MONITOR_STORAGE_LIMIT')
        self.stream.write(data); self.stream.flush(); os.fsync(self.stream.fileno())
        self.digest.update(data); self.used += len(data); self.count += 1

    def finish(self, summary: dict[str, Any]) -> None:
        self.stream.close()
        actual = sha256_bytes((self.output/'observations.jsonl').read_bytes())
        if actual != self.digest.hexdigest():
            raise CaptureError('MONITOR_INTEGRITY_FAILURE')
        report = ('# Alpha Atlas V4 monitoring evidence\n\n'
                  f"Mode: {summary['mode']}\n\nEvidence status: {summary['status']}\n\n"
                  'Stage B and stream-interference acceptance remain UNKNOWN / OPEN.\n\n'
                  'Review summary.json for phase comparisons, gaps, attribution and bounds.\n'
                  'Phase association is owner-declared and unverified; marker references are not verified bytes.\n').encode()
        # Calculate exact final file footprint including manifest; fixed-point on decimal length.
        summary['storage_bytes_consumed'] = 0
        for _ in range(10):
            data = canonical_bytes(summary)
            size = (self.output/'observations.jsonl').stat().st_size+len(data)+len(report)
            manifest_size = sum(64+2+len(name)+1 for name in ('observations.jsonl', 'summary.json', 'report.md'))
            total = size+manifest_size
            if summary['storage_bytes_consumed'] == total:
                break
            summary['storage_bytes_consumed'] = total
        if total > self.limit or len(data)+len(report)+manifest_size > FINAL_RESERVE:
            raise CaptureError('MONITOR_STORAGE_LIMIT')
        self._atomic('summary.json', data)
        self._atomic('report.md', report)
        manifest = ''.join(f"{sha256_bytes((self.output/name).read_bytes())}  {name}\n" for name in
                           ('observations.jsonl', 'summary.json', 'report.md')).encode()
        (self.output/'.incomplete').unlink()
        self._atomic('SHA256SUMS', manifest)
        fd = os.open(self.output, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def verify_evidence(output: Path) -> dict[str, Any]:
    """Offline integrity verification, never a Stage B acceptance certification."""
    if (output/'.incomplete').exists():
        raise CaptureError('MONITOR_INCOMPLETE')
    try:
        lines = (output/'SHA256SUMS').read_text().splitlines()
        expected = ['observations.jsonl', 'summary.json', 'report.md']
        if len(lines) != len(expected):
            raise ValueError()
        for line, name in zip(lines, expected):
            digest, found = line.split('  ')
            if found != name or digest != sha256_bytes((output/name).read_bytes()):
                raise ValueError()
        summary = json.loads((output/'summary.json').read_text())
        rows = [json.loads(line) for line in (output/'observations.jsonl').read_text().splitlines()]
        if (summary['schema_version'] != VERSION or len(rows) != summary['observation_count'] or
                summary['storage_bytes_consumed'] != sum((output/n).stat().st_size for n in [*expected, 'SHA256SUMS'])):
            raise ValueError()
        if len(rows) > MAX_SAMPLES or summary['storage_bytes_consumed'] > MAX_STORAGE:
            raise ValueError()
        for row in rows:
            if (row['schema_version'] != VERSION or row['phase'] not in (*PHASES, 'UNKNOWN_PHASE') or
                    row['quality'] not in ('VALID', 'UNKNOWN') or
                    row['source_type'] not in ('LOCAL_SYNTHETIC_FIXTURE', 'REDIS_HEALTH_GET') or
                    row['health']['status'] not in ('VALID', 'UNKNOWN')):
                raise ValueError()
            stamp(row['observed_at'])
            stamp(row['resources']['observed_at'])
        return summary
    except (OSError, KeyError, ValueError, TypeError, CaptureError):
        raise CaptureError('MONITOR_INTEGRITY_FAILURE') from None


def compare_observations(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """No counter deltas across missing data, changing identities or reset boundaries."""
    comparisons = {p: {'status': 'UNKNOWN', 'intervals': 0, 'elapsed_seconds': 0.0,
                      'counter_changes': {key: 0 for key in COUNTERS}, 'message_changes': {},
                      'last_latency_percentiles': None} for p in PHASES}
    boundaries = []
    previous = None
    last_snapshot = None
    for i, row in enumerate(rows):
        snap = row['health']['snapshot']
        reset = False
        if snap and last_snapshot:
            old = last_snapshot
            reset = (old['connected_at'] != snap['connected_at'] or
                     old['worker_instance_id'] != snap['worker_instance_id'] or
                     any(snap[k] < old[k] for k in COUNTERS) or
                     any(snap['messages_received'].get(k, 0) < old['messages_received'].get(k, 0)
                         for k in set(old['messages_received']) | set(snap['messages_received'])))
            if reset:
                boundaries.append({'observation_index': i, 'observed_at': row['observed_at'],
                                   'reason': 'APPARENT_RESTART_OR_COUNTER_RESET'})
                row['quality'] = 'UNKNOWN'
        if snap:
            last_snapshot = snap
        if not snap or row['health']['status'] != 'VALID':
            previous = None
            continue
        if previous and not reset:
            prior, old = previous
            if row['phase'] == prior['phase'] and row['phase'] in comparisons:
                elapsed = (stamp(snap['updated_at'])-stamp(old['updated_at'])).total_seconds()
                if elapsed > 0:
                    target = comparisons[row['phase']]
                    target['intervals'] += 1; target['elapsed_seconds'] += elapsed
                    for key in COUNTERS:
                        target['counter_changes'][key] += snap[key]-old[key]
                    for key in set(snap['messages_received']) | set(old['messages_received']):
                        target['message_changes'][key] = target['message_changes'].get(key, 0)+snap['messages_received'].get(key, 0)-old['messages_received'].get(key, 0)
                    target['last_latency_percentiles'] = {key: snap[key] for key in LATENCIES}
                elif snap != old:
                    row['quality'] = 'UNKNOWN'
                    boundaries.append({'observation_index': i, 'observed_at': row['observed_at'],
                                       'reason': 'NONINCREASING_HEALTH_TIMESTAMP'})
        previous = row, snap
    for phase, target in comparisons.items():
        relevant = [r for r in rows if r['phase'] == phase]
        if target['elapsed_seconds']:
            target['messages_per_second'] = sum(target['message_changes'].values())/target['elapsed_seconds']
        else:
            target['counter_changes'] = None; target['message_changes'] = None
        if target['intervals'] and all(r['quality'] == 'VALID' for r in relevant):
            target['status'] = 'DESCRIPTIVE_ONLY'
    baseline = comparisons['BASELINE_BEFORE']
    versus_baseline = {}
    for phase, target in comparisons.items():
        relevant = [r for r in rows if r['phase'] == phase]
        processes = [r['resources']['process'] for r in relevant if r['resources'].get('process')]
        filesystems = [r['resources']['filesystem'] for r in relevant if r['resources'].get('filesystem')]
        target['resource_bounds'] = {
            'peak_rss_kib': max((p['peak_rss_kib'] for p in processes), default=None),
            'minimum_free_bytes': min((f['free_bytes'] for f in filesystems), default=None),
            'attribution': 'REFER_TO_OBSERVATION_SOURCE_CLASS_NO_COMPLIANCE_CERTIFICATION'}
        target['connection_observations'] = {
            'connected': sum(bool(r['health']['snapshot']) and r['health']['snapshot']['connection_state'] == 'connected' for r in relevant),
            'unknown_or_not_connected': sum(not r['health']['snapshot'] or r['health']['snapshot']['connection_state'] != 'connected' for r in relevant),
            'continuity': 'UNKNOWN'}
        if phase != 'BASELINE_BEFORE':
            versus_baseline[phase] = {'status': 'UNKNOWN'}
            if baseline['intervals'] and target['intervals']:
                versus_baseline[phase] = {
                    'status': 'DESCRIPTIVE_KNOWN_INTERVALS_ONLY',
                    'messages_per_second_difference': target['messages_per_second']-baseline['messages_per_second'],
                    'counter_changes': target['counter_changes'],
                    'baseline_counter_changes': baseline['counter_changes'],
                    'latency_percentiles': target['last_latency_percentiles'],
                    'baseline_latency_percentiles': baseline['last_latency_percentiles']}
    return {'phases': comparisons, 'versus_baseline': versus_baseline, 'boundaries': boundaries,
            'cross_phase_comparison': 'DESCRIPTIVE_ONLY_REVIEW_REQUIRED',
            'latency_basis': 'CUMULATIVE_HEALTH_PERCENTILES_NOT_PHASE_LOCAL',
            'stream_interference_acceptance': 'UNKNOWN'}


def run_monitor(auth: Mapping[str, Any], output: Path, *, mode: str,
                reader: Callable[[], Any], resources: Callable[[], dict[str, Any]],
                clock: Callable[[], datetime], wait: Callable[[float], None],
                capacity_check: Callable[[], None] = lambda: None) -> dict[str, Any]:
    """Shared bounded engine. Public live entry is observe_passive, which validates scope."""
    writer = EvidenceWriter(output, auth['storage_limit_bytes'])
    rows: list[dict[str, Any]] = []
    start = clock(); end = min(stamp(auth['ends_at']), start+timedelta(seconds=auth['max_duration_seconds']))
    monotonic_end = time.monotonic() + max(0, (end-start).total_seconds())
    last_clock = start
    status, stop = 'COMPLETE_WITH_UNKNOWNS', 'WINDOW_ENDED'
    next_sample = start
    try:
        while clock() < end:
            now = clock()
            if now < last_clock:
                raise CaptureError('MONITOR_CLOCK_REGRESSION')
            last_clock = now
            if mode == 'PASSIVE_OBSERVATION':
                remaining = min((end-now).total_seconds(), monotonic_end-time.monotonic())
                # Connect + read timeout budget; never start Redis I/O near expiration.
                if remaining <= 4:
                    break
            if now < next_sample:
                wait(min((next_sample-now).total_seconds(), (end-now).total_seconds()))
                continue
            if len(rows) >= auth['max_samples'] or len(rows) >= MAX_SAMPLES:
                raise CaptureError('MONITOR_SAMPLE_LIMIT')
            capacity_check()
            # Recheck after filesystem probes; never start a read outside the window.
            if clock() >= end:
                break
            raw = reader()
            observed = clock()
            if observed >= end:
                raise CaptureError('READ_CROSSED_AUTHORIZATION_END')
            health = health_observation(raw, observed, auth['source_binding']['worker_instance_id'])
            phase = phase_at(auth, observed)
            resource = resources()
            resource['observed_at'] = clock().isoformat()
            row = {'schema_version': VERSION, 'observed_at': observed.isoformat(),
                   'source_type': 'LOCAL_SYNTHETIC_FIXTURE' if mode == 'OFFLINE_SYNTHETIC' else 'REDIS_HEALTH_GET',
                   'target_worker': TARGET, 'source_revision': auth['source_revision'],
                   'source_binding_quality': 'SYNTHETIC_ONLY' if mode == 'OFFLINE_SYNTHETIC' else 'OWNER_DECLARED_UNVERIFIED',
                   'phase': phase['phase'] if phase else 'UNKNOWN_PHASE',
                   'workload_association': 'OWNER_DECLARED_UNVERIFIED',
                   'workload_marker_sha256': phase['workload_marker_sha256'] if phase else None,
                   'sampling_delay_seconds': max(0, (now-next_sample).total_seconds()),
                   'quality': 'VALID' if health['status'] == 'VALID' and phase and
                       resource['process_status'] != 'UNKNOWN' and resource['filesystem_status'] != 'UNKNOWN' else 'UNKNOWN',
                   'health': health, 'resources': resource}
            # Detect/reset label before persistence; comparisons must not mutate archived rows later.
            trial = compare_observations([*rows, row])
            if trial['boundaries'] and trial['boundaries'][-1]['observation_index'] == len(rows):
                row['quality'] = 'UNKNOWN'; row['health']['reasons'].append('APPARENT_RESTART_OR_COUNTER_RESET')
            writer.append(row); rows.append(row)
            next_sample = observed+timedelta(seconds=auth['sampling_interval_seconds'])
    except KeyboardInterrupt:
        status, stop = 'INCOMPLETE', 'INTERRUPTED'
    except Exception as exc:
        status = 'INCOMPLETE'
        # Fixed allowlist codes only. Never serialize exception strings/Redis URLs.
        stop = exc.code if isinstance(exc, CaptureError) and exc.code in {
            'MONITOR_SAMPLE_LIMIT', 'MONITOR_STORAGE_LIMIT', 'MONITOR_CAPACITY_EXHAUSTED',
            'READ_CROSSED_AUTHORIZATION_END', 'HEALTH_READ_ERROR', 'HEALTH_SIZE_LIMIT', 'MONITOR_CLOCK_REGRESSION'} else 'OBSERVATION_FAILED'
    comparisons = compare_observations(rows)
    counts = {p: sum(r['phase'] == p for r in rows) for p in PHASES}
    limits = rows[0]['resources']['limits'] if rows else {}
    processes = [r['resources']['process'] for r in rows if r['resources'].get('process')]
    filesystems = [r['resources']['filesystem'] for r in rows if r['resources'].get('filesystem')]
    summary = {'schema_version': VERSION, 'implementation_version': VERSION, 'mode': mode,
               'implementation_module_sha256': sha256_bytes(Path(__file__).read_bytes()),
               'status': status, 'stop_reason': stop, 'source_revision': auth['source_revision'],
               'authorization_id': auth['authorization_id'], 'authorization_sha256': sha256_bytes(canonical_bytes(auth)),
               'observation_start': start.isoformat(), 'observation_end': clock().isoformat(),
               'observation_count': len(rows), 'phase_coverage': counts,
               'gaps': [{'observation_index': i, 'reasons': r['health']['reasons'] or ['RESOURCE_OR_PHASE_ATTRIBUTION_UNKNOWN']}
                        for i, r in enumerate(rows) if r['quality'] != 'VALID'],
               'missing_phases': [p for p, count in counts.items() if count == 0],
               'sampling_delays': [{'observation_index': i, 'delay_seconds': r['sampling_delay_seconds']}
                                   for i, r in enumerate(rows) if r['sampling_delay_seconds'] > 0],
               'comparisons': comparisons,
               'measured_resource_bounds': {'limits': limits, 'attribution': 'SYNTHETIC_ONLY' if mode == 'OFFLINE_SYNTHETIC' else 'OWNER_DECLARED_UNVERIFIED',
                   'peak_rss_kib': max((p['peak_rss_kib'] for p in processes), default=None),
                   'minimum_free_bytes': min((f['free_bytes'] for f in filesystems), default=None)},
               'remaining_unknown': ['WORKER_INTERFERENCE', 'HISTORICAL_STREAM_CONTINUITY', 'PROCESS_LEVEL_RESOURCE_COMPLIANCE',
                                     'WORKLOAD_PHASE_ASSOCIATION', 'PROSPECTIVE_ELIGIBILITY', 'PREMARKET_TIMING'],
               'live_passive_observation': 'NOT_EXECUTED' if mode == 'OFFLINE_SYNTHETIC' else 'OBSERVED_WITH_LIMITATIONS',
               'pilot_consumption': 'PROHIBITED', 'overall_stage_b': 'OPEN',
               'live_provider_requests': 0, 'live_aws_requests': 0}
    writer.finish(summary)
    return verify_evidence(output)


def observe_passive(repo: Path, auth_path: Path, pin: str, output: Path, *,
                    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                    wait: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Not executed by implementation task. Local gates precede sole Redis adapter."""
    try:
        if auth_path.stat().st_size > 16384:
            raise ValueError()
        auth = json.loads(auth_path.read_text())
    except (OSError, ValueError):
        raise CaptureError('MONITOR_AUTH_INVALID') from None
    auth = validate_authorization(auth, pin, now=clock(), source_revision=revision(repo), output=output)
    if output.exists():
        raise CaptureError('MONITOR_OUTPUT_EXISTS')
    limits = registered_limits(repo)
    def capacity() -> None:
        # Reserve entire monitor allowance in addition to Stage B's free-space guard.
        parent = output.parent
        while not parent.exists():
            parent = parent.parent
        stats = os.statvfs(parent)
        if stats.f_bavail*stats.f_frsize < COMBINED_CAP+auth['storage_limit_bytes']:
            raise CaptureError('MONITOR_CAPACITY_EXHAUSTED')
        # Owner-authorized persistent filesystem, not a same-looking root elsewhere.
        expected = auth['resources']['filesystem_device']
        if expected is None or parent.stat().st_dev != expected or not Path('/var/data').is_mount():
            raise CaptureError('MONITOR_CAPACITY_EXHAUSTED')
    capacity()
    # No credential discovery: read only the explicit Redis connection variable after gates.
    url = os.environ.get('REDIS_URL')
    if not url:
        raise CaptureError('MONITOR_REDIS_CONFIGURATION_MISSING')
    try:
        reader = RedisHealthReader(url)
    except Exception:
        raise CaptureError('MONITOR_REDIS_CONFIGURATION_INVALID') from None
    try:
        return run_monitor(auth, output, mode='PASSIVE_OBSERVATION', reader=reader,
                           resources=lambda: local_resources(auth['resources'], limits),
                           clock=clock, wait=wait, capacity_check=capacity)
    finally:
        try:
            reader.close()
        except Exception:
            pass


def synthetic_fixture() -> list[dict[str, Any] | None]:
    """Deterministic healthy, missing/stale, reset and disconnected examples."""
    fixture = []
    start = datetime(2026, 10, 8, tzinfo=UTC)
    for i in range(12):
        now = start+timedelta(seconds=10*i)
        count = i*100 if i < 7 else (i-7)*100
        fixture.append({'schema_version': STREAM_SCHEMA_VERSION, 'worker_instance_id': 'synthetic-worker',
                        'updated_at': now.isoformat(), 'connected_at': (start if i < 7 else start+timedelta(seconds=70)).isoformat(),
                        'last_message_at': now.isoformat(), 'connection_state': 'disconnected' if i == 10 else 'connected',
                        'metrics': {'messages_received': {'T': count}, **{k: (1 if k == 'reconnect_count' and i >= 7 else 0) for k in COUNTERS},
                                    **{k: {'p50': 1., 'p95': 2., 'p99': 3.} for k in LATENCIES}}})
    fixture[4] = None
    fixture[5]['updated_at'] = (start+timedelta(seconds=10)).isoformat()
    fixture[5]['last_message_at'] = fixture[5]['updated_at']
    return fixture


def run_synthetic(repo: Path, output: Path) -> dict[str, Any]:
    if output.resolve().is_relative_to(Path('/var/data')):
        raise CaptureError('SYNTHETIC_RUNTIME_PATH_FORBIDDEN')
    fixture = synthetic_fixture(); start = datetime(2026, 10, 8, tzinfo=UTC); current = [start]; index = [0]
    def read() -> Any:
        value = fixture[index[0]]; index[0] += 1; return value
    def advance(seconds: float) -> None:
        current[0] += timedelta(seconds=seconds)
    auth = {'authorization_id': 'SYNTHETIC_ONLY_NOT_AUTHORIZATION', 'source_revision': 'SYNTHETIC_FIXTURE',
            'ends_at': (start+timedelta(seconds=120)).isoformat(), 'max_duration_seconds': 120,
            'sampling_interval_seconds': 10, 'max_samples': 12, 'storage_limit_bytes': MAX_STORAGE,
            'source_binding': {'worker_instance_id': 'synthetic-worker'},
            'phases': [{'phase': p, 'starts_at': (start+timedelta(seconds=40*i)).isoformat(),
                        'ends_at': (start+timedelta(seconds=40*(i+1))).isoformat(), 'workload_marker_sha256': None}
                       for i, p in enumerate(PHASES)]}
    limits = registered_limits(repo)
    return run_monitor(auth, output, mode='OFFLINE_SYNTHETIC', reader=read,
                       resources=lambda: synthetic_resources(limits), clock=lambda: current[0], wait=advance)
