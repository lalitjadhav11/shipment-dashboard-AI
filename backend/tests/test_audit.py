"""
audit.log_chat_interaction()'s retry-on-bad-reference behavior — mocked
psycopg2.connect, no real DB needed. Covers the pre-existing
ForeignKeyViolation case and the new StringDataRightTruncation case (found
live: an oversized session_id crashed the entire SSE stream uncaught before
this fix — see test_router.py for the primary Pydantic-level fix, this is
the defensive backstop).
"""
import psycopg2
import pytest

from chat import audit


class _FakeCursor:
    def __init__(self, fail_once_with=None):
        self.fail_once_with = fail_once_with
        self.calls = []
        self._n = 0

    def execute(self, sql, params):
        self._n += 1
        self.calls.append(params)
        if self._n == 1 and self.fail_once_with is not None:
            raise self.fail_once_with

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeConn:
    def __init__(self, fail_once_with=None):
        self._cursor = _FakeCursor(fail_once_with)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def _log_kwargs(**overrides):
    kwargs = dict(
        tracking_id="700000000001", customer_id=None, user_query="q", ai_response="a",
        context_snapshot={}, confidence_score=1.0, session_id="a-real-session-id",
    )
    kwargs.update(overrides)
    return kwargs


def test_foreign_key_violation_retries_with_references_nulled(monkeypatch):
    fake_conn = _FakeConn(fail_once_with=psycopg2.errors.ForeignKeyViolation("bad ref"))
    monkeypatch.setattr(audit.psycopg2, "connect", lambda *a, **k: fake_conn)

    audit.log_chat_interaction(**_log_kwargs())

    assert len(fake_conn._cursor.calls) == 2
    retried_params = fake_conn._cursor.calls[1]
    assert retried_params[0] is None  # tracking_id nulled
    assert retried_params[1] is None  # customer_id nulled
    assert fake_conn.rollbacks == 1
    assert fake_conn.commits == 1


def test_string_data_right_truncation_retries_with_session_id_nulled(monkeypatch):
    # Regression for a live-reproduced bug: an oversized session_id (past the
    # shipment_chat_log.session_id VARCHAR(64) column) raised this uncaught
    # and crashed the whole SSE stream. router.py's Pydantic max_length is
    # the primary fix; this is the defensive backstop in case some other
    # caller ever bypasses that validation.
    fake_conn = _FakeConn(fail_once_with=psycopg2.errors.StringDataRightTruncation("too long"))
    monkeypatch.setattr(audit.psycopg2, "connect", lambda *a, **k: fake_conn)

    audit.log_chat_interaction(**_log_kwargs(session_id="x" * 500))

    assert len(fake_conn._cursor.calls) == 2
    retried_params = fake_conn._cursor.calls[1]
    assert retried_params[-1] is None  # session_id nulled
    assert fake_conn.rollbacks == 1
    assert fake_conn.commits == 1


def test_successful_insert_needs_no_retry(monkeypatch):
    fake_conn = _FakeConn(fail_once_with=None)
    monkeypatch.setattr(audit.psycopg2, "connect", lambda *a, **k: fake_conn)

    audit.log_chat_interaction(**_log_kwargs())

    assert len(fake_conn._cursor.calls) == 1
    assert fake_conn.rollbacks == 0
    assert fake_conn.commits == 1


def test_unrelated_db_error_is_not_swallowed(monkeypatch):
    # Only the two specific, "value looked plausible but wasn't" errors
    # should be retried — anything else (e.g. a real schema mismatch) must
    # still surface, not be silently hidden by an overly broad except.
    fake_conn = _FakeConn(fail_once_with=psycopg2.errors.NotNullViolation("oops"))
    monkeypatch.setattr(audit.psycopg2, "connect", lambda *a, **k: fake_conn)

    with pytest.raises(psycopg2.errors.NotNullViolation):
        audit.log_chat_interaction(**_log_kwargs())
