"""Single-destination Option A connections. No connection or DDL at construction.

The approved selection binds a logical endpoint, not a physical server identity.
DNS, TLS trust, credentials and restore isolation remain operator prerequisites.
No service file, host-address override, multi-host failover or arbitrary DSN is
accepted. The same guard runs before driver calls and on every pool checkout.
"""
from __future__ import annotations

import os
import re

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url

from .stream_integrity import IntegrityRejection

_SAFE = frozenset({'user', 'password', 'passfile', 'sslmode', 'sslcert', 'sslkey',
    'sslrootcert', 'sslcrl', 'sslcrldir', 'sslpassword', 'ssl_min_protocol_version',
    'ssl_max_protocol_version', 'channel_binding', 'gssencmode', 'application_name',
    'connect_timeout'})
_ROUTE = frozenset({'host', 'port', 'dbname'})
_ENV_ROUTE = ('PGHOST', 'PGHOSTADDR', 'PGPORT', 'PGDATABASE', 'PGSERVICE',
    'PGSERVICEFILE', 'PGSYSCONFDIR', 'PGOPTIONS', 'PGLOADBALANCEHOSTS', 'PGTARGETSESSIONATTRS')


def reject():
    raise IntegrityRejection('authority_connection_configuration_rejected') from None


def check_environment():
    if any(os.environ.get(name) for name in _ENV_ROUTE):
        reject()


def connection_contract(url):
    """Return an explicit route and allowed scalar auth/TLS options, without I/O."""
    check_environment()
    try:
        url = make_url(url)
        if url.drivername != 'postgresql+psycopg':
            reject()
        host, port, database = url.host, url.port or 5432, url.database
        # Single DNS name or literal IPv4/IPv6 only; no socket/service/conninfo.
        if (not host or not re.fullmatch(r'[a-zA-Z0-9.:-]+', host)
                or not database or not re.fullmatch(r'[a-zA-Z0-9_.-]+', database)
                or not 1 <= port <= 65535 or not url.username):
            reject()
        if any(key not in _SAFE or not isinstance(value, str) or not value
               for key, value in url.query.items()):
            reject()
        options = dict(host=host, port=port, dbname=database, user=url.username)
        if url.password is not None:
            options['password'] = url.password
        options.update(url.query)
        options.setdefault('connect_timeout', 2)
        validate_parameters([], options, dict(host=host, port=port, dbname=database))
        return f'{host}:{port}/{database}', options
    except IntegrityRejection:
        raise
    except Exception:
        reject()


def validate_parameters(args, params, route):
    check_environment()
    if args or set(params) - (_SAFE | _ROUTE):
        reject()
    try:
        if (params.get('host') != route['host'] or params.get('dbname') != route['dbname']
                or int(params.get('port', 5432)) != route['port']
                or any(not isinstance(v, (str, int)) or isinstance(v, bool) for v in params.values())
                or not 1 <= int(params.get('connect_timeout', 2)) <= 2):
            reject()
    except (ValueError, TypeError):
        reject()


def verify_connection(connection, route):
    """Verify the driver's selected logical route, including reused connections."""
    check_environment()
    try:
        info = connection.info
        if info.host != route['host'] or info.dbname != route['dbname'] or int(info.port) != route['port']:
            reject()
    except IntegrityRejection:
        raise
    except Exception:
        reject()


def guard_engine(engine, approved_endpoint):
    if not isinstance(engine, Engine):
        reject()
    identity, options = connection_contract(engine.url)
    if identity != approved_endpoint:
        reject()
    route = {k: options[k] for k in _ROUTE}
    existing = getattr(engine, '_moneybot_authority_route', None)
    if existing is not None:
        if existing != route:
            reject()
        return route
    # SQLAlchemy's psycopg dialect supplies this non-libpq codec context. Only
    # that exact object is permitted; a caller-supplied replacement is rejected.
    codec_context = engine.dialect.create_connect_args(engine.url)[1].get('context')

    def connect(dialect, record, args, params):
        # SQLAlchemy merges explicit connect_args AFTER URL arguments. Check
        # that final mapping, then own the driver invocation so a later handler
        # cannot mutate it after validation. Never retry another destination.
        effective = dict(params)
        context = effective.pop('context', None)
        if context is not codec_context:
            reject()
        effective.setdefault('port', 5432)
        effective.setdefault('connect_timeout', 2)
        validate_parameters(args, effective, route)
        if context is not None:
            effective['context'] = context
        return dialect.loaded_dbapi.connect(**effective)

    def checkout(connection, record, proxy):
        verify_connection(connection, route)

    event.listen(engine, 'do_connect', connect, insert=True)
    event.listen(engine, 'checkout', checkout, insert=True)
    engine._moneybot_authority_route = route
    return route


def create_authority_engine(database_url, selection, *, connect_args=None):
    """Canonical factory for workers, readers and explicit admin operations.

    No custom creator/pool or additional engine kwargs are supported. Programmatic
    SQLAlchemy engines passed directly to PostgresAuthority are trusted objects;
    their final ordinary driver kwargs and pooled connections are still checked.
    """
    try:
        if database_url.startswith('postgres://'):
            database_url = database_url.replace('postgres://', 'postgresql+psycopg://', 1)
        elif database_url.startswith('postgresql://'):
            database_url = database_url.replace('postgresql://', 'postgresql+psycopg://', 1)
        identity, options = connection_contract(database_url)
        if identity != selection.get('endpoint'):
            reject()
        extra = dict(connect_args or {})
        # Factory callers cannot supply any alternate routing source, even an
        # apparently matching duplicate. Credentials/TLS remain configurable.
        if set(extra) - _SAFE:
            reject()
        options.update(extra)
        validate_parameters([], options, {k: options[k] for k in _ROUTE})
        engine = create_engine(database_url, pool_size=2, max_overflow=0,
            pool_timeout=2, pool_pre_ping=False, connect_args=options)
        guard_engine(engine, identity)
        return engine
    except IntegrityRejection:
        raise
    except Exception:
        reject()
