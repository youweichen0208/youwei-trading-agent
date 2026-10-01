"""Resolve release-bound numeric models before the batch freezes evidence.

The legacy vehicle remains explicit compatibility behavior. Fitted models use
only JSON parameters embedded in their immutable release; no fitting, latest
model lookup, provider call, or arbitrary deserialization happens here.
"""

import json
import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from quant.dataset import FEATURE_NAMES, compute_features
from quant.logistic import MODEL_VERSION, FrozenLogisticModel, load_model, predict_model
from quant.models import BASELINE_MODEL_VERSION, QUANT_MODEL_VERSION, predict_quant
from youwei_core.data.calendar import EARLY_CLOSE_AT, ET, REGULAR_CLOSE, REGULAR_OPEN, tzdb_version
from youwei_core.db.meta import calendar_builds
from youwei_core.ledger.sealing import SourcePrediction
from youwei_core.ledger.service import sha256_hex


class ModelRegistryError(ValueError):
    """Release model inputs are inconsistent; no batch prediction is allowed."""


@dataclass(frozen=True)
class ReleasePredictor:
    model_version: str
    models: dict[int, FrozenLogisticModel]
    artifact_hashes: dict[int, str]
    calendar_sessions: list[dict]
    cutoff: datetime
    benchmark_security_id: str

    @property
    def lookback_calendar_days(self) -> int:
        return 120 if self.model_version == MODEL_VERSION else 90

    def predict_case(self, case, bars_by_security: dict[str, list[dict]]) -> SourcePrediction:
        security_id = str(case["security_id"])
        if self.model_version == QUANT_MODEL_VERSION:
            return SourcePrediction(**asdict(predict_quant(bars_by_security.get(security_id, []))))
        features = compute_features(
            bars_by_security=bars_by_security,
            calendar_sessions=self.calendar_sessions,
            cutoff=self.cutoff,
            security_id=security_id,
            benchmark_security_id=self.benchmark_security_id,
        )
        if features["status"] != "available":
            return SourcePrediction(
                source="quant_model", source_status="unavailable", model_version=MODEL_VERSION,
                reason="incomplete_frozen_features",
            )
        model = self.models[case["horizon_td"]]
        matrix = [[features["features"][name] for name in model.spec.feature_names]]
        return SourcePrediction(**asdict(predict_model(model, matrix)[0]))

    def provenance(self, horizon_td: int) -> dict:
        if self.model_version != MODEL_VERSION:
            return {}
        return {
            "quant_model": {
                "model_version": MODEL_VERSION,
                "horizon_td": horizon_td,
                "artifact_sha256": self.artifact_hashes[horizon_td],
                "feature_names": list(self.models[horizon_td].spec.feature_names),
            },
        }


async def build_release_predictor(
    engine: AsyncEngine,
    *,
    release_manifest: dict,
    batch_manifest: dict,
    cutoff: datetime,
    benchmark_security_id: str,
) -> ReleasePredictor:
    if release_manifest.get("baseline_version", BASELINE_MODEL_VERSION) != BASELINE_MODEL_VERSION:
        raise ModelRegistryError("unsupported baseline model version")
    model_version = release_manifest.get("quant_model_version", QUANT_MODEL_VERSION)
    if model_version not in (QUANT_MODEL_VERSION, MODEL_VERSION):
        raise ModelRegistryError("unsupported quant model version")
    models, hashes, sessions = {}, {}, []
    if model_version == MODEL_VERSION:
        try:
            artifacts = release_manifest["quant_artifacts"]
            if set(artifacts) != {"1", "20", "60"}:
                raise ModelRegistryError("quant artifacts must cover exactly horizons 1, 20 and 60")
            for horizon in (1, 20, 60):
                envelope = artifacts[str(horizon)]
                if sha256_hex(envelope["content"]) != envelope["content_sha256"]:
                    raise ModelRegistryError("quant artifact content hash mismatch")
                model = load_model(envelope["content"])
                if model.spec.horizon_td != horizon or model.spec.feature_names != FEATURE_NAMES:
                    raise ModelRegistryError("artifact horizon or feature names differ from the supported contract")
                if datetime.fromisoformat(model.training_cutoff) > cutoff:
                    raise ModelRegistryError("model fit cutoff is later than batch decision cutoff")
                models[horizon] = model
                hashes[horizon] = envelope["content_sha256"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelRegistryError(f"invalid release quant artifacts: {exc}") from exc
        if batch_manifest.get("tzdb_version") != tzdb_version():
            raise ModelRegistryError("batch timezone version differs from the pinned runtime timezone")
        async with engine.begin() as conn:
            calendar = (await conn.execute(select(calendar_builds).where(
                calendar_builds.c.version == batch_manifest.get("calendar_version")
            ))).mappings().one_or_none()
        if calendar is None:
            raise ModelRegistryError("batch frozen calendar build is missing")
        content_hash = hashlib.sha256(calendar.content.encode("utf-8")).hexdigest()
        if content_hash != calendar.content_sha256 or content_hash != batch_manifest.get("calendar_sha256"):
            raise ModelRegistryError("batch frozen calendar content hash mismatch")
        for day in json.loads(calendar.content):
            if not day["is_trading"]:
                continue
            session_date = date.fromisoformat(day["date"])
            sessions.append({
                "date": day["date"],
                "open_utc": datetime.combine(session_date, REGULAR_OPEN, tzinfo=ET).astimezone(UTC).isoformat(),
                "close_utc": datetime.combine(
                    session_date, EARLY_CLOSE_AT if day["early_close"] else REGULAR_CLOSE, tzinfo=ET,
                ).astimezone(UTC).isoformat(),
            })
    return ReleasePredictor(model_version, models, hashes, sessions, cutoff, benchmark_security_id)
