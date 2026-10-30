"""Calendar diagnostics for verified raw evidence, never a qualification approval."""

from yta_alpha.acquisition_store import StoredAcquisition
from yta_alpha.calendar import TradingCalendar
from yta_alpha.massive import aggregate_day


def assess(stored: StoredAcquisition, calendar: TradingCalendar) -> dict:
    acquisition = stored.acquisition
    sessions = calendar.sessions_between(stored.start, stored.end)
    session_set = set(sessions)
    # Coverage is assessed at the latest bar-page retrieval, not a later inspection
    # clock or a later corporate-action response. Per-page finality is checked below.
    observed_at = max(page.retrieved_at for page in acquisition.bars)
    completed = {day for day in sessions if calendar.close_at(day) <= observed_at}
    present, non_sessions, premature = set(), set(), set()
    for page in acquisition.bars:
        for row in page.payload().get("results", []):
            day = aggregate_day(row["t"])
            present.add(day)
            if day not in session_set:
                non_sessions.add(day)
            elif page.retrieved_at < calendar.close_at(day):
                premature.add(day)
    missing = completed - present
    invalid_actions = {event.effective_date for event in acquisition.split_events} - session_set
    return {
        "version": "acquisition-quality-v1",
        "calendar_version": calendar.version,
        "calendar_basis": "XNYS_PROXY_REQUIRES_NVDA_QUALIFICATION",
        "observed_at": observed_at.isoformat(),
        "status": "ISSUES_FOUND"
        if missing or non_sessions or premature or invalid_actions
        else "CHECKS_PASSED",
        "qualification_status": "UNVERIFIED",
        "expected_completed_session_count": len(completed),
        "latest_expected_session": max(completed).isoformat() if completed else None,
        "missing_sessions": [day.isoformat() for day in sorted(missing)],
        "non_session_bars": [day.isoformat() for day in sorted(non_sessions)],
        "premature_bars": [day.isoformat() for day in sorted(premature)],
        "non_session_actions": [day.isoformat() for day in sorted(invalid_actions)],
        "not_yet_due_sessions": [day.isoformat() for day in sessions if day not in completed],
    }
