"""Research invocation capability tokens (S07m-1): Ed25519 asymmetric auth.

The research link (Controller -> Runner research entry, Controller ->
agent-runtime research authorization) uses ED25519 signing so the research
container can VERIFY a grant without holding the ability to SIGN one.

Trust model (architecture §10, hardened from the HMAC capability in
``capability.py``):

- The Controller holds the Ed25519 PRIVATE key and signs every research
  grant. The Runner and the research container hold only the matching PUBLIC
  key (by ``kid``) and can verify — never sign — a token.
- The Runner's control-plane secret and the research container's supplier
  credentials stay with their owning services; neither reaches the other.
- ``kid`` selects a trusted public key from the deployment's configuration
  ONLY. A token never carries its own key or a key-download URL, and an
  unknown ``kid``/``aud``/``alg``/version is rejected outright (no downgrade
  to HMAC, no fallback).

The token binds the invocation's identity (tenant/run/job/attempt/case), the
frozen evidence hash, the execution config version, the audience (Runner
execution vs. runtime research authorization), the granted operation scopes,
and a validity window. Renewal is a NEW signature issued by the Controller
after it re-checks the current attempt — an inner runtime token must never be
extended just because the outer Runner lease was renewed (see S07m).

This module is a pure wire/verification contract (stdlib hashing + the
``cryptography`` package for Ed25519). The optional ``cryptography`` import is
deferred so pure DTO consumers that never touch signing still import cleanly;
callers that sign/verify must declare ``youwei-contracts[signing]``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

# Audience values: which half of the research link a token authorizes.
AUD_RUNNER_EXEC = "runner-exec"          # Controller -> Runner research entry
AUD_RUNTIME_RESEARCH = "runtime-research"  # Controller -> agent-runtime research grant
AUD_RUNNER_TOOLS = "runner-tools"        # agent-runtime experiment container -> Runner tool endpoint (S08)

# Scope values granted on the research link.
SCOPE_RESEARCH_RUN = "research:run"        # run one research turn
SCOPE_RESEARCH_STATUS = "research:status"  # poll a research invocation
SCOPE_RESEARCH_CANCEL = "research:cancel"  # cancel a research invocation

# Scope values for the S08 experiment tool surface (aud=runner-tools).
# Submit / status / read are separately authorized by design: the experiment
# instance holds distinct tokens per operation class, all bound to its
# experiment invocation (invocation_id) and frozen evidence hash.
SCOPE_EXPERIMENT_SUBMIT = "experiment:submit"  # submit one sandboxed computation
SCOPE_EXPERIMENT_STATUS = "experiment:status"  # poll a computation
SCOPE_EXPERIMENT_READ = "experiment:read"      # read one artifact
SCOPE_EXPERIMENT_ADMIN = "experiment:admin"    # control plane: register/terminate an experiment authorization (Controller-side, aud=runner-exec)

# The only accepted algorithm/version. Anything else is rejected (no downgrade).
TOKEN_ALG = "EdDSA"
TOKEN_TYP = "research-v1"
TOKEN_PREFIX = "ywr_"

_ACCEPTED_AUDIENCES = (AUD_RUNNER_EXEC, AUD_RUNTIME_RESEARCH, AUD_RUNNER_TOOLS)


class ResearchCapabilityError(Exception):
    """The research token is malformed, expired, untrusted, or mis-bound."""


@dataclass(frozen=True)
class ResearchCapability:
    """A verified research grant (all binding fields from the signed payload)."""

    kid: str
    aud: str
    invocation_id: uuid.UUID
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    job_id: uuid.UUID
    attempt_no: int
    case_id: uuid.UUID
    evidence_sha256: str
    exec_config_version: str
    scopes: tuple[str, ...]
    exp: datetime
    nbf: datetime


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _ed25519():
    """Deferred import so pure DTO consumers need not install cryptography."""
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.hazmat.primitives import serialization
    except ImportError as exc:  # pragma: no cover - depends on install
        raise ResearchCapabilityError(
            "research capability signing/verification requires the "
            "'cryptography' package; declare youwei-contracts[signing]"
        ) from exc
    return ed25519, serialization


def generate_research_keypair() -> tuple[str, str]:
    """Return (private_key_pem, public_key_pem) for one research signing key.

    The Controller keeps the private key; Runner/research container receive
    only the public key. PEM is the on-disk/deployment representation."""
    ed25519, serialization = _ed25519()
    private = ed25519.Ed25519PrivateKey.generate()
    public = private.public_key()
    priv_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    pub_pem = public.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return priv_pem, pub_pem


def _load_private_key(pem: str):
    ed25519, serialization = _ed25519()
    return serialization.load_pem_private_key(pem.encode("utf-8"), password=None)


def _load_public_key(pem: str):
    ed25519, serialization = _ed25519()
    return serialization.load_pem_public_key(pem.encode("utf-8"))


def public_key_thumbprint(public_key_pem: str) -> str:
    """A short stable kid: sha256 of the DER SubjectPublicKeyInfo."""
    key = _load_public_key(public_key_pem)
    ed25519, serialization = _ed25519()
    der = key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()


def sign_research_token(
    private_key_pem: str,
    *,
    kid: str,
    aud: str,
    invocation_id: uuid.UUID,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    job_id: uuid.UUID,
    attempt_no: int,
    case_id: uuid.UUID,
    evidence_sha256: str,
    exec_config_version: str,
    scopes: tuple[str, ...] | list[str],
    exp: datetime,
    nbf: datetime | None = None,
) -> str:
    """Sign one research grant with the Controller's Ed25519 private key.

    ``evidence_sha256`` binds the frozen evidence; ``exec_config_version``
    binds the deployment's execution config so a config change invalidates old
    tokens. ``aud`` selects which half of the link the token authorizes. The
    resulting token carries ``kid`` in the clear (the verifier selects the
    trusted public key by it) but never carries a key or download URL.
    """
    if aud not in _ACCEPTED_AUDIENCES:
        raise ResearchCapabilityError(f"unknown audience {aud!r}")
    payload = {
        "alg": TOKEN_ALG,
        "typ": TOKEN_TYP,
        "kid": kid,
        "aud": aud,
        "invocation_id": str(invocation_id),
        "tenant_id": str(tenant_id),
        "run_id": str(run_id),
        "job_id": str(job_id),
        "attempt_no": attempt_no,
        "case_id": str(case_id),
        "evidence_sha256": evidence_sha256,
        "exec_config_version": exec_config_version,
        "scopes": sorted(set(scopes)),
        "exp": exp.astimezone(UTC).isoformat(),
        "nbf": (nbf or datetime.now(UTC)).astimezone(UTC).isoformat(),
    }
    body = _canonical(payload)
    key = _load_private_key(private_key_pem)
    sig = key.sign(body)
    return f"{TOKEN_PREFIX}{_b64e(body)}.{_b64e(sig)}"


def _parse_token(token: str) -> tuple[dict, bytes]:
    if not token.startswith(TOKEN_PREFIX):
        raise ResearchCapabilityError("malformed research token (bad prefix)")
    try:
        body_b64, sig_b64 = token[len(TOKEN_PREFIX):].split(".", 1)
        body = _b64d(body_b64)
        sig = _b64d(sig_b64)
    except ValueError as exc:
        raise ResearchCapabilityError("malformed research token") from exc
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ResearchCapabilityError("research token payload is not JSON") from exc
    if not isinstance(payload, dict):
        raise ResearchCapabilityError("research token payload is not an object")
    return payload, sig


def verify_research_token(
    public_keys: dict[str, str],
    token: str,
    *,
    now: datetime | None = None,
) -> ResearchCapability:
    """Verify one research grant against the deployment's trusted public keys.

    ``public_keys`` maps ``kid -> public_key_pem`` and is the ONLY source of
    trust; a token's own ``kid`` is just a selector into it. Unknown kid, alg,
    version, audience, or a failed signature all raise (no HMAC fallback, no
    key-from-token). Expiry/nbf are checked against ``now`` (default: UTC now).
    """
    payload, sig = _parse_token(token)
    if payload.get("alg") != TOKEN_ALG:
        raise ResearchCapabilityError("unsupported research token algorithm")
    if payload.get("typ") != TOKEN_TYP:
        raise ResearchCapabilityError("unsupported research token version")
    kid = payload.get("kid")
    if not isinstance(kid, str) or kid not in public_keys:
        raise ResearchCapabilityError("unknown research signing key (kid)")

    key = _load_public_key(public_keys[kid])
    body = _canonical(payload)
    try:
        key.verify(sig, body)
    except Exception as exc:
        raise ResearchCapabilityError("research token signature mismatch") from exc

    aud = payload.get("aud")
    if aud not in _ACCEPTED_AUDIENCES:
        raise ResearchCapabilityError("unknown research token audience")

    now = (now or datetime.now(UTC)).astimezone(UTC)
    try:
        exp = datetime.fromisoformat(payload["exp"]).astimezone(UTC)
        nbf = datetime.fromisoformat(payload["nbf"]).astimezone(UTC)
    except (KeyError, ValueError) as exc:
        raise ResearchCapabilityError("research token has invalid time bounds") from exc
    if nbf > now:
        raise ResearchCapabilityError("research token not yet valid")
    if exp <= now:
        raise ResearchCapabilityError("research token expired")

    def _uuid(key):
        try:
            return uuid.UUID(payload[key])
        except (KeyError, ValueError, TypeError) as exc:
            raise ResearchCapabilityError(f"research token missing/invalid {key}") from exc

    try:
        attempt_no = int(payload["attempt_no"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ResearchCapabilityError("research token missing/invalid attempt_no") from exc

    evidence_sha256 = payload.get("evidence_sha256")
    exec_config_version = payload.get("exec_config_version")
    if not isinstance(evidence_sha256, str) or not evidence_sha256:
        raise ResearchCapabilityError("research token missing evidence_sha256")
    if not isinstance(exec_config_version, str) or not exec_config_version:
        raise ResearchCapabilityError("research token missing exec_config_version")

    scopes = payload.get("scopes")
    if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
        raise ResearchCapabilityError("research token has invalid scopes")

    return ResearchCapability(
        kid=kid,
        aud=aud,
        invocation_id=_uuid("invocation_id"),
        tenant_id=_uuid("tenant_id"),
        run_id=_uuid("run_id"),
        job_id=_uuid("job_id"),
        attempt_no=attempt_no,
        case_id=_uuid("case_id"),
        evidence_sha256=evidence_sha256,
        exec_config_version=exec_config_version,
        scopes=tuple(sorted(set(scopes))),
        exp=exp,
        nbf=nbf,
    )
