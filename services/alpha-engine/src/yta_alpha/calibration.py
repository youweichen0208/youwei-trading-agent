"""Offline isotonic UP calibration on a reserved window, followed by later evaluation."""

import hashlib
from bisect import bisect_left
from decimal import Decimal, localcontext
from pathlib import Path

from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash, rounded, snapshot_hash
from yta_alpha.labels import build_label
from yta_alpha.training import predict
from yta_alpha.walk_forward import label_id


def _pairs(scores, targets):
    if len(scores) != len(targets):
        raise DomainError("INVALID_CALIBRATION_INPUT", "Scores and targets must align")
    if any(not isinstance(x, Decimal) or not x.is_finite() for x in scores):
        raise DomainError("INVALID_CALIBRATION_INPUT", "Scores require finite Decimals")
    if any(type(y) is not int or y not in (0, 1) for y in targets):
        raise DomainError("INVALID_CALIBRATION_INPUT", "Binary targets must be integers 0 or 1")
    return list(zip(scores, targets, strict=True))


def fit_isotonic(scores, targets):
    """Weighted pool-adjacent-violators; ties share a single score knot."""
    pairs = _pairs(scores, targets)
    if len(pairs) < 2 or len(set(targets)) != 2:
        raise DomainError("INSUFFICIENT_CALIBRATION_DATA", "Calibration requires both classes")
    groups = {}
    for x, y in pairs:
        total, count = groups.get(x, (0, 0))
        groups[x] = (total + y, count + 1)
    xs = sorted(groups)
    blocks = []
    for i, x in enumerate(xs):
        total, count = groups[x]
        blocks.append([i, i, total, count])
        # Compare exact integer sums/weights; no rounded comparisons during fitting.
        while len(blocks) > 1 and blocks[-2][2] * blocks[-1][3] > blocks[-1][2] * blocks[-2][3]:
            right, left = blocks.pop(), blocks.pop()
            blocks.append([left[0], right[1], left[2] + right[2], left[3] + right[3]])
    with localcontext() as context:
        context.prec = 50
        levels = [Decimal(0)] * len(xs)
        for first, last, total, count in blocks:
            for i in range(first, last + 1):
                levels[i] = Decimal(total) / count
        return tuple(zip(xs, levels, strict=True))


def calibrated_probability(knots, score):
    if not isinstance(score, Decimal) or not score.is_finite() or not knots:
        raise DomainError("INVALID_CALIBRATION_INPUT", "Finite score and curve are required")
    if any(
        not isinstance(x, Decimal)
        or not x.is_finite()
        or not isinstance(y, Decimal)
        or not y.is_finite()
        or not 0 <= y <= 1
        for x, y in knots
    ):
        raise DomainError("INVALID_CALIBRATION_INPUT", "Invalid curve values")
    if any(a[0] >= b[0] or a[1] > b[1] for a, b in zip(knots, knots[1:], strict=False)):
        raise DomainError("INVALID_CALIBRATION_INPUT", "Curve must be ordered and monotonic")
    with localcontext() as context:
        context.prec = 50
        index = bisect_left([x for x, _ in knots], score)
        if index == 0:
            return rounded(knots[0][1])
        if index == len(knots):
            return rounded(knots[-1][1])
        x0, y0 = knots[index - 1]
        x1, y1 = knots[index]
        return rounded(y0 + (score - x0) * (y1 - y0) / (x1 - x0))


def reliability(probabilities, targets, *, bins=10):
    pairs = _pairs(probabilities, targets)
    if type(bins) is not int or not 1 <= bins <= 100:
        raise DomainError("INVALID_CALIBRATION_INPUT", "Bin count must be 1 through 100")
    if any(not 0 <= p <= 1 for p in probabilities):
        raise DomainError("INVALID_CALIBRATION_INPUT", "Probabilities must be in [0,1]")
    with localcontext() as context:
        context.prec = 50
        groups = [[] for _ in range(bins)]
        for p, y in pairs:
            groups[min(int(p * bins), bins - 1)].append((p, y))
        rows, error = [], Decimal(0)
        for i, group in enumerate(groups):
            n = len(group)
            mean = sum((p for p, _ in group), Decimal(0)) / n if n else None
            frequency = Decimal(sum(y for _, y in group)) / n if n else None
            if n:
                error += n * abs(mean - frequency)
            rows.append(
                {
                    "lower": str(Decimal(i) / bins),
                    "upper": str(Decimal(i + 1) / bins),
                    "upper_inclusive": i == bins - 1,
                    "count": n,
                    "mean_probability": format(rounded(mean), "f") if n else None,
                    "up_frequency": format(rounded(frequency), "f") if n else None,
                }
            )
        n = len(pairs)
        return {
            "sample_count": n,
            "up_count": sum(targets),
            "bins": rows,
            "brier": format(rounded(sum((p - y) ** 2 for p, y in pairs) / n), "f") if n else None,
            "ece": format(rounded(error / n), "f") if n else None,
        }


def calibrate_and_evaluate(
    artifact,
    calibration_labels,
    evaluation_labels,
    snapshots,
    observations,
    *,
    cutoff,
    calendar,
    embargo_sessions=0,
):
    """Use the model's reserved test fold as calibration, not final evaluation.

    The original artifact's test metrics consequently become calibration diagnostics.
    Final evaluation must occur after every calibration target and availability time.
    """
    if type(embargo_sessions) is not int or not 0 <= embargo_sessions <= 252:
        raise DomainError("INVALID_CALIBRATION_INPUT", "Invalid embargo")
    calibration = tuple(sorted(calibration_labels, key=lambda row: row.base_session))
    evaluation = tuple(sorted(evaluation_labels, key=lambda row: row.base_session))
    if tuple(map(label_id, calibration)) != artifact.fold.test:
        raise DomainError("CALIBRATION_COHORT_MISMATCH", "Use the exact reserved calibration fold")
    if not calibration or not evaluation:
        raise DomainError("INSUFFICIENT_CALIBRATION_DATA", "Both independent windows are required")
    rows = (*calibration, *evaluation)
    if len({r.base_session for r in rows}) != len(rows):
        raise DomainError("CALIBRATION_OVERLAP", "Windows contain duplicate base sessions")
    for group in (calibration, evaluation):
        if tuple(r.base_session for r in group) != calendar.sessions(
            group[0].base_session, group[-1].base_session
        ):
            raise DomainError(
                "INCOMPLETE_CALIBRATION_WINDOW", "Do not silently omit window sessions"
            )
    last_target = max(row.target_session for row in calibration)
    available = max(row.label_available_at for row in calibration)
    if (
        evaluation[0].base_session <= calendar.after(last_target, embargo_sessions)
        or evaluation[0].feature_as_of < available
    ):
        raise DomainError(
            "CALIBRATION_OVERLAP", "Evaluation starts before calibration labels are eligible"
        )
    bases = {snapshot_hash(row): row for row in snapshots}
    outcomes = {snapshot_hash(row): row for row in observations}
    if (
        set(bases) != {r.base_snapshot_hash for r in rows}
        or len(bases) != len(snapshots)
        or set(outcomes) != {r.observation_snapshot_hash for r in rows}
        or len(outcomes) != len(observations)
    ):
        raise DomainError("CALIBRATION_EVIDENCE_MISMATCH", "Supply the exact window evidence")
    for row in rows:
        if (
            row.horizon_sessions != artifact.horizon_sessions
            or row.data_mode != artifact.data_mode
            or row.calendar_version != artifact.calendar_version
        ):
            raise DomainError("COHORT_MISMATCH", "Model and calibration/evaluation cohorts differ")
        rebuilt = build_label(
            bases[row.base_snapshot_hash],
            outcomes[row.observation_snapshot_hash],
            horizon_sessions=row.horizon_sessions,
            cutoff=cutoff,
            calendar=calendar,
        )
        if rebuilt != row:
            raise DomainError(
                "CALIBRATION_EVIDENCE_MISMATCH", "Label differs from its source evidence"
            )
    calibration_scores = tuple(
        Decimal(p["expected_return"])
        for p in predict(
            artifact, tuple(bases[r.base_snapshot_hash] for r in calibration), calendar=calendar
        )
    )
    calibration_targets = tuple(int(r.actual_direction == "UP") for r in calibration)
    knots = fit_isotonic(calibration_scores, calibration_targets)
    evaluation_scores = tuple(
        Decimal(p["expected_return"])
        for p in predict(
            artifact, tuple(bases[r.base_snapshot_hash] for r in evaluation), calendar=calendar
        )
    )
    probabilities = tuple(calibrated_probability(knots, score) for score in evaluation_scores)
    targets = tuple(int(r.actual_direction == "UP") for r in evaluation)
    return {
        "schema_version": "calibration-report-v1",
        "status": "EXPERIMENTAL",
        "qualification": "UNVERIFIED",
        "method": "isotonic-linear-decimal-v1",
        "positive_class": "UP; FLAT and DOWN are 0",
        "out_of_bounds": "endpoint_probability",
        "model_artifact_hash": content_hash(artifact.model_dump(mode="json")),
        "model_sha256": artifact.model_sha256,
        "data_mode": artifact.data_mode.value,
        "horizon_sessions": artifact.horizon_sessions,
        "calendar_version": calendar.version,
        "calibration_ids": list(map(label_id, calibration)),
        "evaluation_ids": list(map(label_id, evaluation)),
        "calibration_available_at": available.isoformat(),
        "cutoff": cutoff.isoformat(),
        "embargo_sessions": embargo_sessions,
        "knots": [[str(x), str(y)] for x, y in knots],
        "probabilities": [format(p, "f") for p in probabilities],
        "calibration_scores": [str(p) for p in calibration_scores],
        "calibration_targets": list(calibration_targets),
        "evaluation_targets": list(targets),
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "reliability": reliability(probabilities, targets),
    }
