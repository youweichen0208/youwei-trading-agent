"""Training manifest registration (S04, campaign-policy §5).

A training manifest FIXES what a model combination is: the feature
sets it may consume (each feature with its kind, definition and
missing-data policy — dependencies are explicit, never implicit), and
per-model preprocessing, label maturation rules, fitting window,
calibration and the model artifacts with content hashes. Every one of
those facts must be stated explicitly; "none" is a declared value,
never a silent default.

A ResearchRelease references the manifest (training_manifest_ref +
sha256) and campaign registration verifies the pair resolves to
registered content, plus consistency of the declared feature set and
model versions (see ledger/service.py). Nothing here approves a model
for formal use — that is the human release approval; formal model
selection additionally requires trial registration (campaign-policy
§5–6).

Registration is idempotent and immutable like release registration:
same id + same content returns the existing row; different content
conflicts; the table is append-only at the DB level.
"""

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from quant.models import BASELINE_MODEL_VERSION, QUANT_MODEL_VERSION
from youwei_core.db.meta import training_manifests
from youwei_core.ledger.service import sha256_hex

VEHICLE_MANIFEST_ID = "tm-vehicles-v0"

# the protocol's five fixed facts per model (campaign-policy §5 /
# S04); each must be an explicit non-blank string
REQUIRED_MODEL_FACTS = (
    "preprocessing",
    "fitting_window",
    "calibration",
    "label_maturation",
    "feature_set",
)


class TrainingManifestError(Exception):
    pass


class ManifestValidationError(TrainingManifestError):
    pass


class TrainingManifestConflict(TrainingManifestError):
    pass


@dataclass
class TrainingManifestRecord:
    manifest_id: str
    training_manifest_row_id: uuid.UUID
    content_sha256: str
    created: bool
    content: dict


# --- hashing and validation -----------------------------------------------------


def training_manifest_content_hash(manifest: dict) -> str:
    """Hash over the canonical manifest content, excluding the hash
    itself (self-excluding, like release_content_hash)."""
    return sha256_hex({k: v for k, v in manifest.items() if k != "content_sha256"})


def _require_nonempty_str(obj, key: str, where: str):
    if not isinstance(obj, dict) or not isinstance(obj.get(key), str):
        raise ManifestValidationError(f"{where}: {key!r} must be a string")
    if not obj[key].strip():
        raise ManifestValidationError(f"{where}: {key!r} must be stated explicitly "
                                      "(use 'none ...' — never blank)")


def _validate_manifest(manifest: dict) -> None:
    """Structural validation at registration: the manifest must carry
    a slug, at least one feature set and one model, and the protocol's
    fixed facts per model. Extra keys are allowed (documentation is
    welcome); required facts are never optional."""
    _require_nonempty_str(manifest, "manifest_id", "manifest")

    feature_sets = manifest.get("feature_sets")
    if not isinstance(feature_sets, list) or not feature_sets:
        raise ManifestValidationError("manifest needs at least one feature set")
    declared_fs = set()
    for i, fs in enumerate(feature_sets):
        where = f"feature_sets[{i}]"
        _require_nonempty_str(fs, "feature_set_version", where)
        if fs["feature_set_version"] in declared_fs:
            raise ManifestValidationError(
                f"{where}: duplicate feature_set_version {fs['feature_set_version']!r}"
            )
        declared_fs.add(fs["feature_set_version"])
        features = fs.get("features")
        if not isinstance(features, list) or not features:
            raise ManifestValidationError(f"{where}: needs at least one feature")
        for j, feature in enumerate(features):
            fwhere = f"{where}.features[{j}]"
            for key in ("name", "kind", "definition", "missing_policy"):
                _require_nonempty_str(feature, key, fwhere)

    models = manifest.get("models")
    if not isinstance(models, list) or not models:
        raise ManifestValidationError("manifest needs at least one model")
    for i, model in enumerate(models):
        where = f"models[{i}]"
        _require_nonempty_str(model, "role", where)
        _require_nonempty_str(model, "model_version", where)
        for key in REQUIRED_MODEL_FACTS:
            _require_nonempty_str(model, key, where)
        if model["feature_set"] != "none" and model["feature_set"] not in declared_fs:
            raise ManifestValidationError(
                f"{where}: feature_set {model['feature_set']!r} is not declared "
                "by this manifest"
            )
        artifact = model.get("artifact")
        if not isinstance(artifact, dict):
            raise ManifestValidationError(f"{where}: artifact must be an object")
        for key in ("kind", "ref"):
            _require_nonempty_str(artifact, key, f"{where}.artifact")
        sha = artifact.get("sha256")
        if not isinstance(sha, str) or len(sha) != 64 or any(
            c not in "0123456789abcdef" for c in sha
        ):
            raise ManifestValidationError(
                f"{where}.artifact: sha256 must be 64 hex chars"
            )


# --- the frozen vehicles -----------------------------------------------------------


def quant_module_artifact() -> dict:
    """The artifact record pinning the quant package's model module:
    the hash covers the module file's bytes, so any change to the
    vehicles' code changes the hash and requires a new registration."""
    import quant.models as module

    data = Path(module.__file__).read_bytes()
    return {
        "kind": "python-module",
        "ref": "quant.models",
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def vehicle_training_manifest() -> dict:
    """The frozen engineering vehicles' training manifest: everything
    the protocol requires is stated explicitly, 'none' where nothing
    happens. These are pipeline vehicles for engineering acceptance —
    deterministic, fully disclosed, NOT formal models; formal
    selection requires trial registration and a human-approved
    release."""
    artifact = quant_module_artifact()
    return {
        "manifest_id": VEHICLE_MANIFEST_ID,
        "description": (
            "Frozen engineering vehicles for the Phase 1A pipeline. "
            "Not formal models: formal selection requires trial "
            "registration and a human-approved release "
            "(campaign-policy 5-6)."
        ),
        "feature_sets": [
            {
                "feature_set_version": "momentum-20d-v0",
                "features": [
                    {
                        "name": "momentum_20d",
                        "kind": "daily_bars",
                        "definition": (
                            "last close / first close - 1 over the final 20 "
                            "closes of the evidence window; 21 quality-ok "
                            "bars with close > 0 required"
                        ),
                        "window_sessions": 20,
                        "missing_policy": (
                            "fewer than 21 valid bars -> quant_model "
                            "unavailable with reason=insufficient_history; "
                            "never a fabricated value"
                        ),
                    }
                ],
            }
        ],
        "models": [
            {
                "role": "baseline",
                "model_version": BASELINE_MODEL_VERSION,
                "artifact": artifact,
                "feature_set": "none",
                "preprocessing": (
                    "none: the constant base rate consumes no evidence "
                    "(p=0.5 independently of market history)"
                ),
                "fitting_window": "none: no fitted parameters",
                "calibration": "none: fixed p=0.5",
                "label_maturation": "none: no training labels consumed",
            },
            {
                "role": "quant_model",
                "model_version": QUANT_MODEL_VERSION,
                "artifact": artifact,
                "feature_set": "momentum-20d-v0",
                "preprocessing": (
                    "quality-ok bars with close > 0, chronological; computed "
                    "from the batch's frozen evidence snapshot (forward view "
                    "at the decision cutoff)"
                ),
                "fitting_window": "none: the mapping has no fitted parameters",
                "calibration": (
                    "p = clip(0.5 + momentum, 0.05, 0.95); "
                    "expected_excess = clip(momentum, -0.5, 0.5)"
                ),
                "label_maturation": (
                    "none: no training labels (vehicle); forward labels come "
                    "from the outcome resolver total-return-v1 at the D20 "
                    "exit plus the registered grace period"
                ),
            },
        ],
    }


# --- registration -------------------------------------------------------------------


async def register_training_manifest(
    engine: AsyncEngine, *, manifest: dict
) -> TrainingManifestRecord:
    """Idempotent registration. Same id + same content -> existing;
    same id + different content -> conflict. Nothing is ever updated."""
    if not isinstance(manifest, dict):
        raise ManifestValidationError("manifest must be an object")
    _validate_manifest(manifest)
    content_sha = training_manifest_content_hash(manifest)
    manifest_id = manifest["manifest_id"]

    async with engine.begin() as conn:
        existing = (
            await conn.execute(
                select(training_manifests).where(
                    training_manifests.c.manifest_id == manifest_id
                )
            )
        ).mappings().first()
        if existing is not None:
            if existing.content_sha256 != content_sha:
                raise TrainingManifestConflict(
                    f"training manifest {manifest_id!r} already exists with "
                    "different content; register a new manifest_id instead of "
                    "silently changing a fixed one"
                )
            return TrainingManifestRecord(
                manifest_id=manifest_id,
                training_manifest_row_id=existing.id,
                content_sha256=content_sha,
                created=False,
                content=existing.content,
            )
        row_id = uuid.uuid4()
        await conn.execute(
            training_manifests.insert().values(
                id=row_id,
                manifest_id=manifest_id,
                content=manifest,
                content_sha256=content_sha,
            )
        )
    return TrainingManifestRecord(
        manifest_id=manifest_id,
        training_manifest_row_id=row_id,
        content_sha256=content_sha,
        created=True,
        content=manifest,
    )


# --- queries ---------------------------------------------------------------------


async def get_training_manifest(
    engine: AsyncEngine, manifest_id: str
) -> dict | None:
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                select(training_manifests).where(
                    training_manifests.c.manifest_id == manifest_id
                )
            )
        ).mappings().first()
    if row is None:
        return None
    return {
        "manifest_id": row.manifest_id,
        "training_manifest_row_id": str(row.id),
        "content": row.content,
        "content_sha256": row.content_sha256,
        "created_at": row.created_at.isoformat(),
    }
