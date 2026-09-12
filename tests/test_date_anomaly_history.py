"""
Tests for app.db.date_anomaly_history - the "Diff Date Anomaly" analysis
history table (separate from app.db.script_history - see that module's
docstring for the distinction). Uses a real sqlite file in a pytest
tmp_path, same pattern as tests/test_script_history.py, since this is
thin plain-sqlite3 code where a fake/mock would just re-describe the SQL
rather than catch real mistakes in it.
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import date_anomaly_history as dah


def _db_path(tmp_path) -> str:
    return str(tmp_path / "scriptgen_internal.db")


def test_record_analysis_returns_id_and_round_trips(tmp_path):
    db_path = _db_path(tmp_path)
    entry_id = dah.record_analysis(
        db_path, username="alice", niss="10450618-301", threshold=0, anomaly_count=2,
        correct_date=datetime.date(2024, 3, 1), correct_date_reading=999,
        explanation="What's wrong: ...", source=dah.SOURCE_WEB,
    )
    assert isinstance(entry_id, int)

    entry = dah.get_analysis(db_path, entry_id)
    assert entry is not None
    assert entry.niss == "10450618-301"
    assert entry.username == "alice"
    assert entry.anomaly_count == 2
    assert entry.correct_date == "2024-03-01"
    assert entry.correct_date_reading == "999"
    assert entry.generated is False
    assert entry.item_count is None  # not known yet at Detect time
    assert entry.source == dah.SOURCE_WEB


def test_record_analysis_with_no_correct_date_leaves_it_none(tmp_path):
    db_path = _db_path(tmp_path)
    entry_id = dah.record_analysis(
        db_path, username="alice", niss="N2", threshold=0, anomaly_count=0, source=dah.SOURCE_WEB,
    )
    entry = dah.get_analysis(db_path, entry_id)
    assert entry.correct_date is None
    assert entry.correct_date_reading is None


def test_update_analysis_refines_fields_without_clearing_others(tmp_path):
    db_path = _db_path(tmp_path)
    entry_id = dah.record_analysis(
        db_path, username="alice", niss="10450618-301", threshold=0, anomaly_count=2,
        correct_date=datetime.date(2024, 3, 1), explanation="detect-stage explanation", source=dah.SOURCE_WEB,
    )

    # Resolve-stage update: fills in item/xml counts, leaves `generated` alone.
    dah.update_analysis(db_path, entry_id, item_count=1, xml_count=1, explanation="resolve-stage explanation")
    entry = dah.get_analysis(db_path, entry_id)
    assert entry.item_count == 1
    assert entry.xml_count == 1
    assert entry.generated is False  # untouched by this call
    assert entry.explanation == "resolve-stage explanation"
    assert entry.anomaly_count == 2  # untouched - update_analysis doesn't take this field at all

    # Generate-stage update: sets generated=True, doesn't clobber item_count
    # even though it isn't passed again this time.
    dah.update_analysis(db_path, entry_id, generated=True, explanation="generate-stage explanation")
    entry = dah.get_analysis(db_path, entry_id)
    assert entry.generated is True
    assert entry.item_count == 1  # still there from the earlier update
    assert entry.explanation == "generate-stage explanation"


def test_update_analysis_on_unknown_id_does_not_raise(tmp_path):
    db_path = _db_path(tmp_path)
    dah.update_analysis(db_path, 999999, generated=True)  # no matching row - should just no-op


def test_update_analysis_with_no_fields_is_a_noop(tmp_path):
    db_path = _db_path(tmp_path)
    entry_id = dah.record_analysis(db_path, username="alice", niss="N1", threshold=0, anomaly_count=1)
    before = dah.get_analysis(db_path, entry_id)
    dah.update_analysis(db_path, entry_id)  # nothing passed
    after = dah.get_analysis(db_path, entry_id)
    assert before.updated_at_utc == after.updated_at_utc


def test_list_analyses_most_recent_first(tmp_path):
    db_path = _db_path(tmp_path)
    id1 = dah.record_analysis(db_path, username="alice", niss="N1", threshold=0, anomaly_count=1)
    id2 = dah.record_analysis(db_path, username="alice", niss="N2", threshold=0, anomaly_count=1)
    # Touch N1 again so it becomes the most-recently-updated row.
    dah.update_analysis(db_path, id1, generated=True)

    entries = dah.list_analyses(db_path, username="alice")
    assert [e.niss for e in entries] == ["N1", "N2"]
    assert entries[0].id == id1


def test_list_analyses_scopes_to_username(tmp_path):
    db_path = _db_path(tmp_path)
    dah.record_analysis(db_path, username="alice", niss="N1", threshold=0, anomaly_count=1)
    dah.record_analysis(db_path, username="bob", niss="N2", threshold=0, anomaly_count=1)

    alice_entries = dah.list_analyses(db_path, username="alice")
    assert [e.niss for e in alice_entries] == ["N1"]

    everyone = dah.list_analyses(db_path)
    assert {e.niss for e in everyone} == {"N1", "N2"}


def test_get_analysis_missing_id_returns_none(tmp_path):
    db_path = _db_path(tmp_path)
    assert dah.get_analysis(db_path, 999999) is None


def test_record_analysis_one_shot_for_batch(tmp_path):
    # Batch's use pattern: every field known at once, generated=True from
    # the start - no separate update_analysis call needed.
    db_path = _db_path(tmp_path)
    entry_id = dah.record_analysis(
        db_path, username="alice", niss="N1", threshold=0, anomaly_count=1,
        item_count=1, xml_count=1, item_status_count=1, anomalous_count=0,
        generated=True, explanation="full explanation", source=dah.SOURCE_BATCH,
    )
    entry = dah.get_analysis(db_path, entry_id)
    assert entry.generated is True
    assert entry.item_count == 1
    assert entry.item_status_count == 1
    assert entry.source == dah.SOURCE_BATCH
