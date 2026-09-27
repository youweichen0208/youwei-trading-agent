"""Per-job capability tokens (S02b).

HMAC-SHA256 signed, stdlib only. A capability binds exactly one
(job, attempt, tenant) with explicit scopes and an expiry that must
not outlive the attempt's lease. Downstream systems (agent runtime,
llm gateway, sandbox runner) verify before accepting job requests:
the token is the ONLY thing a job's context may present — request
parameters alone never prove authority (architecture section 10).
"""

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime


class CapabilityError(Exception):
    pass


@dataclass(frozen=True)
class Capability:
    job_id: uuid.UUID
    attempt_no: int
    tenant_id: uuid.UUID
    scopes: tuple[str, ...]
    exp: datetime


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_capability(
    secret: bytes | str,
    *,
    job_id: uuid.UUID,
    attempt_no: int,
    tenant_id: uuid.UUID,
    scopes: tuple[str, ...] | list[str],
    exp: datetime,
) -> str:
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    payload = {
        "job_id": str(job_id),
        "attempt_no": attempt_no,
        "tenant_id": str(tenant_id),
        "scopes": list(scopes),
        "exp": exp.astimezone(UTC).isoformat(),
    }
    body = _canonical(payload)
    sig = hmac.new(secret, body, hashlib.sha256).digest()
    return f"ywc_{_b64e(body)}.{_b64e(sig)}"


def verify_capability(secret: bytes | str, token: str) -> Capability:
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    if not token.startswith("ywc_"):
        raise CapabilityError("malformed token")
    try:
        body_b64, sig_b64 = token[4:].split(".", 1)
        body = _b64d(body_b64)
        sig = _b64d(sig_b64)
    except ValueError as exc:
        raise CapabilityError("malformed token") from exc

    expected = hmac.new(secret, body, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        raise CapabilityError("signature mismatch")

    payload = json.loads(body)
    exp = datetime.fromisoformat(payload["exp"])
    if exp <= datetime.now(UTC):
        raise CapabilityError("capability expired")

    return Capability(
        job_id=uuid.UUID(payload["job_id"]),
        attempt_no=int(payload["attempt_no"]),
        tenant_id=uuid.UUID(payload["tenant_id"]),
        scopes=tuple(payload["scopes"]),
        exp=exp,
    )
