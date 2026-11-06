"""Option A authority: synchronized PostgreSQL with intact durable storage.

No DDL or initialization occurs on construction/startup. Administrative methods
require an independently supplied selection/initialization plan. This protocol
cannot detect coherent rollback of the selected PostgreSQL history itself.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Callable

from sqlalchemy import (BigInteger, Column, Integer, MetaData, String, Table, UniqueConstraint,
                        Text, delete, func, insert, select, update)

from .stream_integrity import IntegrityRejection, MAX_WATERMARKS
from .stream_postgres_connection import guard_engine, verify_connection

SCHEMA = 1
MAX_PAYLOAD_BYTES = 16_384
MAX_TICKET = 9_000_000_000_000_000_000
metadata = MetaData()
control = Table('market_authority_control', metadata,
    Column('namespace', String(128), primary_key=True),
    Column('body', Text, nullable=False))
fields = Table('market_authority_fields', metadata,
    Column('namespace', String(128), primary_key=True),
    Column('field', String(80), primary_key=True),
    Column('position', Integer, nullable=False),
    Column('ordinal', BigInteger, nullable=False),
    Column('body', Text, nullable=False),
    UniqueConstraint('namespace', 'position', name='market_authority_position_unique'))


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def reject(reason):
    raise IntegrityRejection(reason)


def endpoint_identity(url):
    """Credential-free routing identity; a restore at the SAME endpoint is excluded."""
    return f'{url.host}:{url.port or 5432}/{url.database}'


def valid_field(key):
    if not isinstance(key, str) or not re.fullmatch(r'(Q|T|A|AM):[A-Z0-9./_-]{1,32}', key):
        reject('invalid_authority_field')


class PostgresAuthority:
    def __init__(self, engine, selection: dict, *, clock: Callable = None, local_test=False):
        self.engine, self.selection = engine, deepcopy(selection)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.local_test = local_test
        if local_test and engine.dialect.name != 'sqlite':
            reject('offline_sqlite_adapter_required')
        if not (engine.dialect.name == 'postgresql' or (local_test and engine.dialect.name == 'sqlite')):
            reject('postgresql_authority_required')
        self.namespace = selection.get('namespace', '')
        self._selection()
        self._route = None if local_test else guard_engine(engine, selection['endpoint'])

    def _selection(self):
        s = self.selection
        try:
            expiry = datetime.fromisoformat(s['expires_at'])
            now = self.clock()
            valid = (s['schema'] == SCHEMA and s['contract'] == 'OPTION_A_DEVELOPMENT'
                and type(s['generation']) is int and s['generation'] > 0
                and type(s['capacity']) is int and 1 <= s['capacity'] <= MAX_WATERMARKS
                and re.fullmatch(r'[a-zA-Z0-9:_-]{1,128}', self.namespace)
                and re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', s['instance_id'])
                and expiry.utcoffset() is not None and now.utcoffset() is not None and now < expiry
                and s['endpoint'] == endpoint_identity(self.engine.url))
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            reject('authority_selection_invalid_or_expired')

    @contextmanager
    def transaction(self, *, write=False, ready=True, provisioning=False):
        self._selection()
        if not self.local_test:
            guard_engine(self.engine, self.selection['endpoint'])
        try:
            with self.engine.connect() as conn:
                # SQLite is an explicitly enabled OFFLINE transaction test adapter.
                # Production uses logged PG tables, fresh READ COMMITTED snapshots,
                # and one namespace lock to serialize cross-channel floors/eviction.
                if self.local_test:
                    conn.exec_driver_sql('BEGIN IMMEDIATE')
                else:
                    verify_connection(conn.connection.driver_connection, self._route)
                    conn = conn.execution_options(isolation_level='READ COMMITTED')
                    conn.begin()
                    conn.exec_driver_sql("SET LOCAL search_path = public, pg_catalog")
                    conn.exec_driver_sql("SET LOCAL synchronous_commit = 'on'")
                    conn.exec_driver_sql("SET LOCAL statement_timeout = '2000ms'")
                    conn.exec_driver_sql("SET LOCAL lock_timeout = '1000ms'")
                    settings = conn.exec_driver_sql("SELECT current_setting('fsync'), current_setting('full_page_writes'), pg_is_in_recovery() ").one()
                    if tuple(settings) != ('on', 'on', False):
                        reject('authority_durability_settings_invalid')
                    storage = conn.exec_driver_sql("SELECT relname, relpersistence FROM pg_class WHERE oid IN ('market_authority_control'::regclass, 'market_authority_fields'::regclass)").all()
                    if sorted(tuple(row) for row in storage) != [('market_authority_control', 'p'), ('market_authority_fields', 'p')]:
                        reject('authority_logged_tables_required')
                try:
                    query = select(control.c.body).where(control.c.namespace == self.namespace)
                    raw = conn.execute(query.with_for_update(read=not write)).scalar_one_or_none()
                    root = json.loads(raw) if raw is not None else None
                    if not provisioning:
                        self._validate_root(conn, root, ready=ready)
                    yield conn, root
                    conn.commit()  # response failure is UNKNOWN; never retry automatically
                except BaseException:
                    conn.rollback()
                    raise
        except IntegrityRejection:
            raise
        except Exception:
            # Driver exceptions can embed connection options. Retain only the
            # protocol outcome; never expose credential-bearing exception text.
            raise IntegrityRejection('authority_transaction_outcome_unknown') from None

    def _validate_root(self, conn, root, *, ready=True, selected=True):
        if not isinstance(root, dict) or root.get('schema') != SCHEMA:
            reject('authority_metadata_missing_or_incompatible')
        if selected and any(root.get(k) != self.selection[k] for k in ('generation', 'instance_id', 'capacity', 'endpoint')):
            reject('authority_generation_or_endpoint_fenced')
        if root.get('phase') not in {'PREPARING', 'READY', 'QUARANTINED'} or (ready and root['phase'] != 'READY'):
            reject('authority_not_ready')
        for key in ('ticket', 'retired', 'count'):
            if type(root.get(key)) is not int or root[key] < 0:
                reject('authority_metadata_invalid')
        # Old candidate roots may be inspected only by explicit initialization.
        # Normal readers/writers must never silently assume a missing floor is 0.
        if (('recovery_floor' not in root and selected)
                or type(root.get('recovery_floor', 0)) is not int
                or not 0 <= root.get('recovery_floor', 0) <= root['retired']):
            reject('authority_recovery_floor_invalid')
        count, ordinal = conn.execute(select(func.count(), func.max(fields.c.ordinal)).where(fields.c.namespace == self.namespace)).one()
        if (root['count'] != count or count > root['capacity'] or root['ticket'] > MAX_TICKET
                or (ordinal is not None and ordinal > root['ticket'])):
            reject('authority_index_inconsistent')

    def _root_save(self, conn, root):
        conn.execute(update(control).where(control.c.namespace == self.namespace).values(body=encode(root)))

    def _row(self, conn, key, *, legacy=False):
        raw = conn.execute(select(fields).where(fields.c.namespace == self.namespace, fields.c.field == key)).mappings().one_or_none()
        if raw is None:
            return None
        row = json.loads(raw['body'])
        if legacy:
            row.setdefault('price_floor', 0)
        if (row.get('field') != key or row.get('position') != raw['position'] or row.get('ordinal') != raw['ordinal']
                or not 0 <= raw['position'] < self.selection['capacity']
                or type(row.get('bound')) is not int or row['bound'] < 0 or type(row.get('quality_revision')) is not int
                or type(row.get('price_floor')) is not int or row['price_floor'] < 0):
            reject('authority_row_inconsistent')
        for name in ('confirmed', 'pending'):
            op = row.get(name)
            if op is not None and (op.get('field') != key or op.get('stamp', -1) > row['bound']
                    or type(op.get('ticket')) is not int or not 0 < op['ticket'] <= row['ordinal']
                    or op.get('slot') not in (2 * row['position'], 2 * row['position'] + 1)
                    or op.get('fingerprint') != digest({k:v for k,v in op.items() if k != 'fingerprint'})):
                reject('authority_operation_inconsistent')
        return row

    def _save(self, conn, row, *, new=False):
        values = dict(position=row['position'], ordinal=row['ordinal'], body=encode(row))
        if new:
            conn.execute(insert(fields).values(namespace=self.namespace, field=row['field'], **values))
        else:
            conn.execute(update(fields).where(fields.c.namespace == self.namespace, fields.c.field == row['field']).values(**values))

    def initialize(self, plan):
        """ADMIN ONLY; schema must have been separately installed. No startup call.

        Two durable phases allow interruption/resume. Generation is the bounded
        receipt-consumption counter. Reusing an old plan cannot reset history.
        """
        required = ('history_sha256', 'writer_exclusion_sha256', 'selection_approval_sha256')
        if not isinstance(plan, dict) or set(plan) - set(required) - {'selection', 'previous_generation', 'floor', 'aggregate_contexts'}:
            reject('initialization_plan_invalid')
        if any(not re.fullmatch('[0-9a-f]{64}', str(plan.get(k, ''))) for k in required):
            reject('initialization_evidence_required')
        if (plan.get('selection') != self.selection or type(plan.get('floor')) is not int or plan['floor'] < 0
                or plan.get('previous_generation') != self.selection['generation'] - 1):
            reject('initialization_plan_invalid')
        contexts = plan.get('aggregate_contexts', {})
        if not isinstance(contexts, dict) or len(contexts) > self.selection['capacity']:
            reject('initialization_contexts_invalid')
        for key, value in contexts.items():
            valid_field(key)
            if (key.split(':')[0] not in {'A', 'AM'} or not isinstance(value, dict)
                    or set(value) != {'stamp', 'sequence', 'event_id'}
                    or type(value['stamp']) is not int or not 0 <= value['stamp'] <= plan['floor']
                    or type(value['sequence']) is not int or value['sequence'] < 0
                    or not isinstance(value['event_id'], str) or len(value['event_id']) > 128):
                reject('initialization_contexts_invalid')
        receipt = digest(plan)
        with self.transaction(write=True, ready=False, provisioning=True) as (conn, root):
            if root is not None:
                self._validate_root(conn, root, ready=False, selected=False)
                if root['generation'] == self.selection['generation'] and root.get('receipt') == receipt:
                    return root['phase']  # exact idempotent resume, no reset of later prices
                if root['generation'] != plan['previous_generation'] or root['phase'] == 'PREPARING':
                    reject('initialization_receipt_consumed_or_conflicting')
                if root['capacity'] != self.selection['capacity']:
                    reject('authority_capacity_change_requires_separate_migration')
                # Carry every acknowledged AND unknown bound into a conservative
                # floor before retaining historical rows in this transaction.
                bounds = [json.loads(v)['bound'] for v in conn.execute(select(fields.c.body).where(fields.c.namespace == self.namespace)).scalars()]
                floor = max([root['retired'], plan['floor'], *bounds])
                recovery_floor = floor
                ticket = root['ticket']
                count = root['count']
                if contexts:
                    reject('existing_authority_context_replacement_forbidden')
                for key in conn.execute(select(fields.c.field).where(fields.c.namespace == self.namespace)).scalars().all():
                    row = self._row(conn, key, legacy='recovery_floor' not in root)
                    row['quality_revision'] += 1
                    row['bound'] = max(row['bound'], recovery_floor)
                    row['price_floor'] = max(row['price_floor'], recovery_floor)
                    row.update(hold='generation_changed_requires_current_confirmation', stale=True)
                    self._save(conn, row)
            else:
                if plan['previous_generation'] != 0:
                    reject('missing_prior_authority_history')
                if conn.execute(select(func.count()).select_from(fields).where(fields.c.namespace == self.namespace)).scalar_one():
                    reject('orphan_authority_rows')
                floor, ticket, count = plan['floor'], 0, len(contexts)
                recovery_floor = plan['floor']
                # Trusted, explicitly reviewed migration ordering context is NOT
                # a price confirmation. Missing context continues to fail closed.
                for position, (key, context) in enumerate(sorted(contexts.items())):
                    self._save(conn, dict(field=key, position=position, ordinal=0,
                        bound=max(context['stamp'], recovery_floor), price_floor=recovery_floor,
                        uncertain=True, confirmed=None, pending=None,
                        quality_revision=0, hold='migration_requires_current_confirmation', stale=True,
                        context=context), new=True)
            new_root = dict(schema=SCHEMA, generation=self.selection['generation'], instance_id=self.selection['instance_id'],
                endpoint=self.selection['endpoint'], capacity=self.selection['capacity'], phase='PREPARING',
                ticket=ticket, retired=floor, recovery_floor=recovery_floor, count=count, receipt=receipt)
            if root is None:
                conn.execute(insert(control).values(namespace=self.namespace, body=encode(new_root)))
            else:
                self._root_save(conn, new_root)
        return 'PREPARING'

    def activate(self, plan):
        with self.transaction(write=True, ready=False) as (conn, root):
            if root.get('receipt') != digest(plan) or root['phase'] not in {'PREPARING', 'READY'}:
                reject('initialization_receipt_mismatch')
            if type(plan.get('floor')) is not int or root['recovery_floor'] < plan['floor']:
                reject('initialization_recovery_floor_mismatch')
            for key in conn.execute(select(fields.c.field).where(fields.c.namespace == self.namespace)).scalars().all():
                row = self._row(conn, key)
                if min(row['bound'], row['price_floor']) < root['recovery_floor']:
                    reject('initialization_row_below_recovery_floor')
            root['phase'] = 'READY'
            self._root_save(conn, root)

    def read(self, key=None):
        if key is not None:
            valid_field(key)
        with self.transaction() as (conn, root):
            row = self._row(conn, key) if key is not None else None
            return deepcopy(root), row

    def reserve(self, key, stamp, payload, owner, revision, *, now, recovery=False):
        valid_field(key)
        if (type(stamp) is not int or stamp < 0 or stamp > now or not isinstance(owner, str)
                or not re.fullmatch(r'[0-9a-f-]{36}', owner) or type(revision) is not int or revision < 1
                or len(encode(payload).encode()) > MAX_PAYLOAD_BYTES):
            reject('invalid_authority_proposal')
        with self.transaction(write=True) as (conn, root):
            row = self._row(conn, key)
            bound = max(row['bound'], row['price_floor'], root['recovery_floor']) if row else root['retired']
            if recovery:
                # Includes a retired opposite Q/T channel even if target remains.
                bound = max(bound, root['retired'])
                for channel in ('Q', 'T', 'A', 'AM'):
                    other = self._row(conn, channel + ':' + key.split(':', 1)[1])
                    if other: bound = max(bound, other['bound'])
            head = row['confirmed'] if row else None
            equal_ok = (row and not row['uncertain'] and head and head['stamp'] == bound
                        and head['generation'] == root['generation']
                        and head['owner'] == owner and revision > head['revision'] and not recovery
                        and head['stamp'] > row['price_floor'])
            if stamp < bound or (stamp == bound and not equal_ok):
                reject('timestamp_integrity_hold')
            if head and head['owner'] == owner and revision <= head['revision']:
                reject('authority_revision_reused')
            pending = row['pending'] if row else None
            if pending and pending['owner'] == owner and revision <= pending['revision']:
                reject('authority_revision_reused')
            new = row is None
            if new:
                used = conn.execute(select(fields.c.position).where(fields.c.namespace == self.namespace)).scalars().all()
                if len(used) == root['capacity']:
                    victim_key = conn.execute(select(fields.c.field).where(fields.c.namespace == self.namespace).order_by(fields.c.ordinal).limit(1)).scalar_one()
                    victim = self._row(conn, victim_key)
                    # Pending operations may retire: their bound survives, and
                    # their later confirmations fail the field/ticket CAS.
                    obligation = max(victim['bound'], victim['price_floor'])
                    root['retired'] = max(root['retired'], obligation)
                    # Transfer the retired channel's acknowledged/uncertain
                    # price obligation to each bounded surviving sibling. Keep
                    # their independent ordering bounds/context unchanged.
                    symbol = victim_key.split(':', 1)[1]
                    for channel in ('Q', 'T', 'A', 'AM'):
                        sibling_key = channel + ':' + symbol
                        if sibling_key == victim_key:
                            continue
                        sibling = self._row(conn, sibling_key)
                        if sibling is not None:
                            sibling['price_floor'] = max(sibling['price_floor'], obligation)
                            self._save(conn, sibling)
                    position = victim['position']
                    conn.execute(delete(fields).where(fields.c.namespace == self.namespace, fields.c.field == victim_key))
                    root['count'] -= 1
                    if stamp <= root['retired']:
                        reject('retirement_integrity_hold')
                else:
                    position = next(i for i in range(root['capacity']) if i not in used)
                row = dict(field=key, position=position, bound=0, price_floor=root['recovery_floor'], uncertain=False, confirmed=None,
                           pending=None, quality_revision=0, hold=None, stale=False)
                root['count'] += 1
            if root['ticket'] == MAX_TICKET:
                reject('authority_ticket_exhausted')
            root['ticket'] += 1
            slot = 2 * row['position'] + (1 - head['slot'] % 2 if head else 0)
            op = dict(field=key, generation=root['generation'], ticket=root['ticket'], stamp=stamp,
                      owner=owner, revision=revision, payload=deepcopy(payload), slot=slot,
                      quality_revision=row['quality_revision'], recovery=bool(recovery))
            op['fingerprint'] = digest(op)
            row.update(bound=max(row['bound'], stamp), uncertain=True, pending=op, ordinal=root['ticket'])
            self._save(conn, row, new=new); self._root_save(conn, root)
            return op

    def confirm(self, op):
        # Caller must possess an exact Redis staging response in this invocation.
        with self.transaction(write=True) as (conn, root):
            row = self._row(conn, op['field'])
            if row is None or row['pending'] != op or op['generation'] != root['generation']:
                reject('authority_pending_cas_changed')
            if op['stamp'] <= row['price_floor'] or op['stamp'] < root['recovery_floor']:
                reject('authority_price_floor_at_confirmation')
            if op['recovery']:
                bound = root['retired']
                for channel in ('Q', 'T', 'A', 'AM'):
                    other = self._row(conn, channel + ':' + op['field'].split(':', 1)[1])
                    if other:
                        bound = max(bound, other['bound'])
                if bound > op['stamp']:
                    reject('recovery_integrity_hold_at_confirmation')
            row.update(confirmed=op, pending=None, uncertain=False)
            if row['quality_revision'] == op['quality_revision']:
                row.update(hold=None, stale=False)
            self._save(conn, row)

    def hold(self, key, reason, *, owner=None, expected_ticket=None, stale=False):
        valid_field(key)
        if not isinstance(reason, str) or len(reason) > 200:
            reject('invalid_authority_hold')
        with self.transaction(write=True) as (conn, root):
            row = self._row(conn, key)
            if row is None: return
            head = row['confirmed']
            if expected_ticket is not None and (head is None or head['ticket'] != expected_ticket): return
            if owner is not None and head and head['owner'] != owner: return
            row['quality_revision'] += 1
            row['hold'], row['stale'] = reason, row['stale'] or stale
            self._save(conn, row)

    def quarantine(self):
        with self.transaction(write=True, ready=False) as (conn, root):
            root['phase'] = 'QUARANTINED'; self._root_save(conn, root)
