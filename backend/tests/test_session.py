"""
Phase 3 session memory. get_agent_cursor is mocked with a fake row (no real
DB needed) — the follow-up detector itself is pure regex/entity logic.
"""
from chat import session, entities


def _entities(**kwargs) -> entities.ExtractedEntities:
    e = entities.ExtractedEntities()
    for k, v in kwargs.items():
        setattr(e, k, v)
    return e


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def execute(self, *args, **kwargs):
        pass

    def fetchone(self):
        return self._row


class _FakeCursorCM:
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return _FakeCursor(self._row)

    def __exit__(self, *args):
        return False


def _fake_get_agent_cursor(row):
    return lambda: _FakeCursorCM(row)


def _raising_get_agent_cursor():
    def _raise():
        raise ConnectionError("db unreachable")
    return _raise


# --- wants_session_context -------------------------------------------------

def test_followup_pronoun_with_no_entities_wants_context():
    assert session.wants_session_context("what about it", _entities()) is True
    assert session.wants_session_context("is that one delayed too", _entities()) is True


def test_query_with_its_own_tracking_id_does_not_want_context():
    assert session.wants_session_context(
        "where is it, 700000000001", _entities(tracking_id="700000000001"),
    ) is False


def test_fleet_wide_language_blocks_context_even_with_pronoun():
    # "it" appears nowhere here, but this guards the more dangerous case:
    # a query that WOULD match the pronoun regex but is clearly fleet-wide.
    assert session.wants_session_context(
        "what about all the delayed shipments", _entities(),
    ) is False


def test_query_with_enum_match_does_not_want_context():
    assert session.wants_session_context(
        "is that customs hold", _entities(enum_matches={"current_status": "CUSTOMS_HOLD"}),
    ) is False


def test_long_query_does_not_want_context():
    long_query = "what about it and also can you tell me a lot more detail about the whole situation please"
    assert session.wants_session_context(long_query, _entities()) is False


def test_no_pronoun_no_entities_does_not_want_context():
    assert session.wants_session_context("what's our on-time percentage", _entities()) is False


# --- get_last_tracking_id ----------------------------------------------------

def test_get_last_tracking_id_returns_value(monkeypatch):
    monkeypatch.setattr(session, "get_agent_cursor", _fake_get_agent_cursor({"tracking_id": "700000000001"}))
    assert session.get_last_tracking_id("session-abc") == "700000000001"


def test_get_last_tracking_id_none_when_no_row(monkeypatch):
    monkeypatch.setattr(session, "get_agent_cursor", _fake_get_agent_cursor(None))
    assert session.get_last_tracking_id("session-abc") is None


def test_get_last_tracking_id_degrades_on_db_error(monkeypatch):
    monkeypatch.setattr(session, "get_agent_cursor", _raising_get_agent_cursor())
    assert session.get_last_tracking_id("session-abc") is None
