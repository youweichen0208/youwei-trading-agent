"""Mode-scoped report projection after full immutable calibration replay."""

from yta_alpha.calibration_contracts import CalibrationReportResponse
from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash


def calibration_report(registry, identity, mode):
    # Reject an out-of-scope identity before loading artifacts or native inference.
    with registry.models._connect() as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        row = connection.execute(
            "SELECT id FROM yta.calibration_records WHERE id=%s AND data_mode=%s",
            (identity, mode.value),
        ).fetchone()
    if row is None:
        raise DomainError("CALIBRATION_NOT_FOUND", "Calibration report not found", status=404)
    payload, registered_at = registry.get_with_timestamp(identity)
    report = payload["report"]
    if report["data_mode"] != mode.value:
        raise DomainError("CALIBRATION_INTEGRITY_ERROR", "Calibration report mode differs")
    return CalibrationReportResponse(
        id=identity,
        model_id=payload["model_id"],
        registered_at=registered_at.isoformat(),
        content_hash=content_hash(payload),
        report_hash=content_hash(report),
        report=report,
    )
