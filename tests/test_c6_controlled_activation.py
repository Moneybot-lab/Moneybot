"""Current integration activation and reader capability regressions."""
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from moneybot.services.market_stream import create_stream_state
from moneybot.services.stream_integrity import IntegrityRejection
from moneybot.services.stream_activation import ReaderAuthorityState
from moneybot.services.live_market import LiveQuoteResolver


@pytest.fixture(autouse=True)
def configuration(monkeypatch):
    monkeypatch.delenv('MASSIVE_STREAM_AUTHORITY', raising=False)
    monkeypatch.delenv('MASSIVE_STREAM_PROCESS_ROLE', raising=False)


def test_absent_activation_cannot_write_or_promote_rest():
    state = create_stream_state('redis://unused.invalid')
    with pytest.raises(IntegrityRejection):
        state.set_latest(None, ttl_seconds=30)
    now = datetime(2026, 11, 4, 16, tzinfo=timezone.utc)
    quote = LiveQuoteResolver(state=state, clock=lambda: now, rest_quote=lambda _: {
        'price': 100, 'source_mode': 'rest', 'source': 'synthetic',
        'event_timestamp': now.isoformat(), 'live_data_available': True,
        'is_stale': False}).resolve('AAPL')
    assert quote.price == 100
    assert quote.admission_status == 'UNKNOWN'
    assert quote.is_degraded and quote.is_stale


@pytest.mark.parametrize('mode', ['legacy', 'unknown', ''])
def test_legacy_and_invalid_modes_rejected(monkeypatch, mode):
    monkeypatch.setenv('MASSIVE_STREAM_AUTHORITY', mode)
    with pytest.raises(IntegrityRejection): create_stream_state(None)


def test_writer_requires_separate_activation_and_role(monkeypatch):
    with pytest.raises(IntegrityRejection): create_stream_state(None, writer=True)
    monkeypatch.setenv('MASSIVE_STREAM_AUTHORITY', 'postgres_option_a')
    with pytest.raises(IntegrityRejection, match='explicit_writer_role'):
        create_stream_state('redis://unused.invalid', writer=True)


@pytest.mark.parametrize('operation', ['set_latest', 'mark_symbols_stale', 'reconcile_price',
    'hold_quality', 'set_health', 'publish_updates', 'authority', 'client'])
def test_reader_does_not_delegate_mutation_or_raw_clients(operation):
    underlying = Mock()
    reader = ReaderAuthorityState(underlying)
    with pytest.raises(IntegrityRejection, match='reader_cannot_mutate'):
        getattr(reader, operation)
    assert underlying.mock_calls == []


def test_factory_defaults_to_reader_even_in_writer_process(monkeypatch):
    from moneybot.services import stream_postgres_state
    underlying = Mock()
    monkeypatch.setenv('MASSIVE_STREAM_AUTHORITY', 'postgres_option_a')
    monkeypatch.setenv('MASSIVE_STREAM_PROCESS_ROLE', 'writer')
    monkeypatch.setattr(stream_postgres_state, 'from_environment', lambda _: underlying)
    assert isinstance(create_stream_state('redis://unused.invalid'), ReaderAuthorityState)
    assert create_stream_state('redis://unused.invalid', writer=True) is underlying


def test_progressive_snapshot_is_degraded_and_advice_cannot_change_prices(tmp_path):
    import json
    from scripts.benchmark_portfolio_watchlist import scenario
    with scenario(tmp_path, 2, 2, 'success', 0, 0) as s:
        s.app.extensions['market_stream_state'] = create_stream_state(None)
        response = s.client.get('/api/user-watchlist/stream', buffered=False)
        try:
            events = [json.loads(chunk) for chunk in response.response]
        finally:
            response.close()
        assert events[0]['type'] == 'snapshot'
        for row in events[0]['enriched_items']:
            assert row['current_price'] is not None
            assert row['live_market']['admission_status'] == 'UNKNOWN'
            assert row['live_market']['is_degraded'] is True
        updates = [event['item'] for event in events if event['type'] == 'advice']
        assert len(updates) == 2
        for update in updates:
            assert not {'current_price', 'live_market', 'history30', 'shares',
                        'performance_amount', 'position_weight_percent'} & update.keys()


def test_analysis_quote_availability_does_not_claim_authority(monkeypatch):
    from moneybot.services.market_data import MarketDataService
    service = object.__new__(MarketDataService)
    original = {'price': 123, 'live_data_available': True, 'is_stale': False}
    monkeypatch.setattr(service, '_get_provider_quote', lambda _: original)
    result = service.get_quote('AAA')
    assert result['price'] == 123 and result['live_data_available'] is True
    assert result['admission_status'] == 'UNKNOWN' and result['is_degraded']
    assert 'price_authority_unverified' in result['quality_flags']
    assert 'admission_status' not in original
