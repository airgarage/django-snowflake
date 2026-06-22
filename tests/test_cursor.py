"""Tests for TimingCursorWrapper's query-path heartbeat hook."""
from django_snowflake.cursor import TimingCursorWrapper
from django_snowflake.heartbeat import HEARTBEAT


class FakeCursor:
    def __init__(self):
        self.calls = []
        self.closed = False

    def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "RESULT"

    def executemany(self, sql, param_list):
        self.calls.append(("executemany", sql, param_list))
        return "RESULT_MANY"

    def close(self):
        self.closed = True


def test_execute_without_params_records_query_and_delegates(monkeypatch):
    recorded = []
    monkeypatch.setattr(HEARTBEAT, "record_query", lambda alias: recorded.append(alias))
    cursor = FakeCursor()

    result = TimingCursorWrapper(cursor, "snowflake").execute("SELECT 1")

    assert result == "RESULT"
    assert recorded == ["snowflake"]
    assert cursor.calls == [("execute", "SELECT 1", ())]


def test_execute_with_params_passes_params_through(monkeypatch):
    monkeypatch.setattr(HEARTBEAT, "record_query", lambda alias: None)
    cursor = FakeCursor()

    TimingCursorWrapper(cursor, "snowflake").execute("SELECT %s", [42])

    assert cursor.calls == [("execute", "SELECT %s", ([42],))]


def test_executemany_records_query_and_delegates(monkeypatch):
    recorded = []
    monkeypatch.setattr(HEARTBEAT, "record_query", lambda alias: recorded.append(alias))
    cursor = FakeCursor()

    result = TimingCursorWrapper(cursor, "snowflake").executemany("INSERT", [(1,), (2,)])

    assert result == "RESULT_MANY"
    assert recorded == ["snowflake"]
    assert cursor.calls == [("executemany", "INSERT", [(1,), (2,)])]


def test_context_manager_closes_cursor():
    cursor = FakeCursor()
    with TimingCursorWrapper(cursor, "snowflake") as wrapper:
        assert wrapper.cursor is cursor
    assert cursor.closed is True
