import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from yta_alpha.acquisition_diff import compare
from yta_alpha.acquisition_store import StoredAcquisition
from yta_alpha.errors import DomainError
from yta_alpha.massive import Acquisition, RawPage, normalize_splits
from yta_alpha.models import DataMode

START, END = date(2024, 1, 2), date(2024, 1, 5)
NOW = datetime(2024, 1, 6, tzinfo=UTC)


def sample(days=(0,), *, action=True):
    rows = [
        dict(o=100, h=105, l=99, c=101, v=1000, t=1704171600000 + day * 86400000) for day in days
    ]
    bars = RawPage("fixture", (), NOW, json.dumps({"results": rows}).encode())
    actions = (
        [
            {
                "id": "split-1",
                "ticker": "NVDA",
                "execution_date": "2024-01-03",
                "split_from": 1,
                "split_to": 10,
                "adjustment_type": "forward_split",
            }
        ]
        if action
        else []
    )
    splits = RawPage("fixture", (), NOW, json.dumps({"results": actions}).encode())
    return StoredAcquisition(
        START,
        END,
        DataMode.REPLAY,
        Acquisition((bars,), (splits,), normalize_splits([splits], START, END)),
    )


def alter_bars(value, **changes):
    return replace(
        value,
        acquisition=replace(
            value.acquisition, bars=(replace(value.acquisition.bars[0], **changes),)
        ),
    )


def test_precise_price_change_is_reported_as_decimal_string_without_mutating_inputs():
    before = sample()
    raw = before.acquisition.bars[0].body
    after = alter_bars(before, body=raw.replace(b'"c": 101', b'"c": 101.000000000000000001'))
    report = compare(before, after)
    assert report["status"] == "DATA_CHANGED" and report["raw_pages_changed"] is True
    assert report["bar_changes"][0]["key"] == "2024-01-02"
    assert report["bar_changes"][0]["before"]["close"] == "101"
    assert report["bar_changes"][0]["after"]["close"] == "101.000000000000000001"
    assert report["action_changes"] == [] and report["qualification_status"] == "UNVERIFIED"
    assert before.acquisition.bars[0].body == raw
    assert compare(after, before)["bar_changes"][0]["after"]["close"] == "101"
    json.dumps(report)  # No Decimal instances or binary floats escape the wire boundary.


def test_formatting_and_numeric_scale_are_not_financial_revisions():
    before = sample()
    after = alter_bars(
        before, body=before.acquisition.bars[0].body.replace(b'"c": 101', b'"c": 101.0') + b" "
    )
    report = compare(before, after)
    assert report["status"] == "NO_DATA_CHANGE" and report["raw_pages_changed"] is True
    assert report["bar_changes"] == report["action_changes"] == []


def test_later_identical_retrieval_is_not_a_data_revision():
    before = sample()
    after = alter_bars(before, retrieved_at=NOW + timedelta(days=1))
    report = compare(before, after)
    assert report["status"] == "NO_DATA_CHANGE" and report["raw_pages_changed"] is False
    assert report["before_retrieved_at"] != report["after_retrieved_at"]


def test_added_and_missing_dates_are_explicit_and_sorted():
    report = compare(sample((0, 1)), sample((0, 2)))
    assert [change["key"] for change in report["bar_changes"]] == ["2024-01-03", "2024-01-04"]
    assert report["bar_changes"][0]["after"] is None
    assert report["bar_changes"][1]["before"] is None


def test_action_ratio_revision_preserves_identity():
    before = sample()
    page = before.acquisition.splits[0]
    page = replace(page, body=page.body.replace(b'"split_to": 10', b'"split_to": 20'))
    after = replace(
        before,
        acquisition=replace(
            before.acquisition, splits=(page,), split_events=normalize_splits([page], START, END)
        ),
    )
    report = compare(before, after)
    assert report["bar_changes"] == []
    assert report["action_changes"][0] == {
        "key": "split-1",
        "before": {
            "effective_date": "2024-01-03",
            "split_from": "1",
            "split_to": "10",
            "adjustment_type": "forward_split",
        },
        "after": {
            "effective_date": "2024-01-03",
            "split_from": "1",
            "split_to": "20",
            "adjustment_type": "forward_split",
        },
    }


@pytest.mark.parametrize("added", [True, False])
def test_added_or_absent_action_does_not_guess_adjustment(added):
    report = compare(sample(action=not added), sample(action=added))
    event = report["action_changes"][0]
    assert event["key"] == "split-1"
    assert event["before" if added else "after"] is None
    assert report["qualification_status"] == "UNVERIFIED"


@pytest.mark.parametrize(
    "change",
    [
        {"start": date(2024, 1, 3)},
        {"end": date(2024, 1, 4)},
        {"data_mode": DataMode.LIVE_RESEARCH},
    ],
)
def test_different_scope_is_not_misreported_as_missing_data(change):
    before = sample()
    with pytest.raises(DomainError) as error:
        compare(before, replace(before, **change))
    assert error.value.code == "ACQUISITION_SCOPE_MISMATCH"
