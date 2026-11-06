"""Explicit process capabilities for the controlled C6 integration.

Missing activation keeps web accounting available with UNKNOWN price quality.
It never chooses the legacy Redis authority. No connections are made here.
"""
from .market_stream import InMemoryMarketStreamState
from .stream_integrity import IntegrityRejection


class InactiveAuthorityState(InMemoryMarketStreamState):
    requires_authority = True

    def set_latest(self, *args, **kwargs):
        raise IntegrityRejection('authority_not_activated')

    def event_quality(self, event):
        return 'UNKNOWN'

    def integrity_preflight(self, **kwargs):
        raise IntegrityRejection('authority_not_activated')


class ReaderAuthorityState:
    """Read/demand capability; publication and maintenance are not delegated."""
    requires_authority = True
    _allowed = frozenset({'get_latest', 'get_health', 'event_quality',
        'integrity_quality', 'register_demand', 'clear_demand', 'desired_demand',
        'integrity_preflight'})

    def __init__(self, state):
        self.__state = state

    def __getattr__(self, name):
        if name in self._allowed:
            return getattr(self.__state, name)
        raise IntegrityRejection('reader_cannot_mutate_authority')
