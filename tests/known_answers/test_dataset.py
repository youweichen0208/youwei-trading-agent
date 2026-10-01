"""Known answers at the frozen-bars to training-rows boundary."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from quant.dataset import build_dataset, compute_features, total_return_from_bars


NY = ZoneInfo("America/New_York")
CUTOFF = "2024-08-31T10:00:00+00:00"


def inputs():
    sessions = []
    bars = {"stock": [], "spy": []}
    day = date(2024, 4, 1)
    while day <= date(2025, 1, 31):
        if day.weekday() < 5 and day != date(2024, 9, 2):
            sessions.append(
                {
                    "date": day.isoformat(),
                    "open_utc": datetime.combine(day, time(9, 30), NY).isoformat(),
                    "close_utc": datetime.combine(day, time(16), NY).isoformat(),
                }
            )
            for values in bars.values():
                values.append(
                    {
                        "trade_date": day.isoformat(),
                        "open": 100,
                        "close": 100,
                        "div_cash": 0,
                        "split_factor": 1,
                        "quality": "ok",
                        "volume": 1000,
                    }
                )
        day += timedelta(days=1)
    return bars, sessions


def test_dataset_respects_fixed_window_entry_rights_and_split_dividend_wealth():
    bars, sessions = inputs()
    for bar in bars["stock"]:
        if bar["trade_date"] == "2024-09-03":
            # These actions occurred before this case entered; neither counts.
            bar.update(div_cash=9, split_factor=3)
        if bar["trade_date"] >= "2024-09-04":
            bar.update(open=51, close=51)
        if bar["trade_date"] == "2024-09-04":
            bar.update(div_cash=1, split_factor=2)

    result = build_dataset(
        bars_by_security=bars,
        calendar_sessions=sessions,
        cutoffs=[CUTOFF],
        panel_security_ids=["stock"],
        benchmark_security_id="spy",
    )
    assert len(result["rows"]) == 3
    first, primary, long = result["rows"]
    assert first["entry_at"] == "2024-09-03T13:30:00+00:00"
    assert first["exit_at"] == "2024-09-03T20:00:00+00:00"
    assert first["label"] == 0
    assert primary["exit_at"] == "2024-09-30T20:00:00+00:00"
    # A $100 unit becomes two shares worth $51 plus $1 reinvested per share.
    assert primary["excess_return"] == pytest.approx(0.04)
    assert primary["label"] == 1
    assert primary["assumed_label_available_at"] == "2024-10-07T20:00:00+00:00"
    assert long["label_status"] == "resolved"
    assert primary["features"] == {
        "excess_momentum_20d": 0.0,
        "excess_momentum_60d": 0.0,
        "volatility_20d": 0.0,
    }
    assert result["metadata"]["data_mode"] == "historical_source"
    assert result["metadata"]["evaluation_scope"] == "retrospective_current_panel"


def test_missing_feature_day_and_entry_price_keep_the_planned_row():
    bars, sessions = inputs()
    bars["stock"] = [
        bar for bar in bars["stock"]
        if bar["trade_date"] not in {"2024-08-15", "2024-09-03"}
    ]
    result = build_dataset(
        bars_by_security=bars,
        calendar_sessions=sessions,
        cutoffs=[CUTOFF],
        panel_security_ids=["stock"],
        benchmark_security_id="spy",
    )
    assert len(result["rows"]) == 3
    assert result["metadata"]["planned_rows"] == 3
    for row in result["rows"]:
        assert row["feature_status"] == "unavailable"
        assert row["label_status"] == "unresolved"
        assert row["label"] is None
        assert row["entry_at"] == "2024-09-03T13:30:00+00:00"
        assert {(m.get("trade_date"), m["stage"]) for m in row["missing"]} == {
            ("2024-08-15", "features"),
            ("2024-09-03", "label"),
        }
        assert all(m["reason"] == "missing_bar" for m in row["missing"])


@pytest.mark.parametrize("change", [
    {"close": "NaN"},
    {"close": 0},
    {"split_factor": 0},
    {"div_cash": -1},
    {"volume": 0},
    {"quality": "unsupported_merger"},
])
def test_invalid_or_unsupported_action_bar_never_becomes_a_label(change):
    bars, sessions = inputs()
    next(b for b in bars["stock"] if b["trade_date"] == "2024-09-03").update(change)
    result = build_dataset(
        bars_by_security=bars, calendar_sessions=sessions, cutoffs=[CUTOFF],
        panel_security_ids=["stock"], benchmark_security_id="spy",
    )
    assert len(result["rows"]) == 3
    for row in result["rows"]:
        assert row["label_status"] == "unresolved"
        assert row["label"] is None
        assert row["missing"][0]["reason"] == "invalid_bar"


def test_features_use_21_and_61_closes_and_population_daily_volatility():
    bars, sessions = inputs()
    history = [bar for bar in bars["stock"] if bar["trade_date"] <= "2024-08-30"]
    history[-61]["close"] = 50
    history[-21]["close"] = 80
    # A last-20-bars implementation would lose the $80 start and return zero.
    # The 20 daily returns are +25% once and zero 19 times.
    for bar in bars["stock"]:
        if bar["trade_date"] > "2024-08-30":
            bar.update(close=999999, div_cash=50000, split_factor=100)
    result = compute_features(
        bars_by_security=bars, calendar_sessions=sessions, cutoff=CUTOFF,
        security_id="stock", benchmark_security_id="spy",
    )
    assert result["status"] == "available"
    assert result["features"]["excess_momentum_20d"] == pytest.approx(0.25)
    assert result["features"]["excess_momentum_60d"] == pytest.approx(1.0)
    assert result["features"]["volatility_20d"] == pytest.approx(0.8649421946)


def test_declared_session_times_drive_dst_and_early_close():
    bars, sessions = inputs()
    next(s for s in sessions if s["date"] == "2024-11-11")["close_utc"] = "2024-11-11T18:00:00+00:00"
    result = build_dataset(
        bars_by_security=bars, calendar_sessions=sessions,
        cutoffs=["2024-11-09T11:00:00+00:00"],
        panel_security_ids=["stock"], benchmark_security_id="spy",
    )
    row = result["rows"][0]
    assert row["entry_at"] == "2024-11-11T14:30:00+00:00"
    assert row["exit_at"] == "2024-11-11T18:00:00+00:00"


def test_total_return_known_answer_and_explicit_calendar_gaps():
    assert float(total_return_from_bars([
        {"open": 100, "close": 100, "split_factor": 3, "div_cash": 9},
        {"open": 50, "close": 51, "split_factor": 2, "div_cash": 1},
    ])) == pytest.approx(0.04)
    bars, sessions = inputs()
    result = build_dataset(
        bars_by_security=bars,
        calendar_sessions=[s for s in sessions if s["date"] < "2024-09-04"],
        cutoffs=[CUTOFF], panel_security_ids=["stock"], benchmark_security_id="spy",
    )
    assert [row["label_status"] for row in result["rows"]] == ["unresolved"] * 3
    assert result["rows"][0]["missing"][-1]["reason"] == "missing_calendar_grace_deadline"
    assert result["rows"][1]["missing"][-1]["reason"] == "missing_calendar_exit"


@pytest.mark.parametrize("overrides", [
    {"horizons_td": [0]},
    {"horizons_td": [20, 20]},
    {"panel_security_ids": ["stock", "stock"]},
    {"cutoffs": [CUTOFF, "2024-08-31T06:00:00-04:00"]},
])
def test_invalid_plan_cannot_silently_duplicate_or_shift_cases(overrides):
    bars, sessions = inputs()
    arguments = dict(
        bars_by_security=bars, calendar_sessions=sessions, cutoffs=[CUTOFF],
        panel_security_ids=["stock"], benchmark_security_id="spy",
    )
    arguments.update(overrides)
    with pytest.raises(ValueError):
        build_dataset(**arguments)
