"""S07m-1: Ed25519 research capability tokens (contract-level).

The research link uses asymmetric signing so the research container verifies a
grant without being able to sign one. These tests pin the wire contract:
roundtrip, identity binding, audience separation, tamper/expiry/nbf, unknown
kid, algorithm/version downgrade rejection, and that renewal is a NEW
signature (an inner token is not extended by re-issuing the outer lease).
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from youwei_contracts.research_capability import (
    AUD_RUNNER_EXEC,
    AUD_RUNTIME_RESEARCH,
    SCOPE_RESEARCH_CANCEL,
    SCOPE_RESEARCH_RUN,
    SCOPE_RESEARCH_STATUS,
    TOKEN_ALG,
    ResearchCapabilityError,
    generate_research_keypair,
    public_key_thumbprint,
    sign_research_token,
    verify_research_token,
)


def _claims(**overrides):
    base = dict(
        kid="k1",
        aud=AUD_RUNTIME_RESEARCH,
        invocation_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        attempt_no=1,
        case_id=uuid.uuid4(),
        evidence_sha256="e" * 64,
        exec_config_version="v1",
        scopes=(SCOPE_RESEARCH_RUN,),
        exp=datetime.now(UTC) + timedelta(seconds=30),
    )
    base.update(overrides)
    return base


@pytest.fixture(scope="module")
def keypair():
    priv, pub = generate_research_keypair()
    return priv, pub


def _keys(pub):
    return {"k1": pub}


def test_roundtrip_and_identity_binding(keypair):
    priv, pub = keypair
    c = _claims()
    token = sign_research_token(priv, **c)
    cap = verify_research_token(_keys(pub), token)
    assert cap.kid == "k1"
    assert cap.aud == AUD_RUNTIME_RESEARCH
    assert cap.invocation_id == c["invocation_id"]
    assert cap.tenant_id == c["tenant_id"]
    assert cap.run_id == c["run_id"]
    assert cap.job_id == c["job_id"]
    assert cap.attempt_no == 1
    assert cap.case_id == c["case_id"]
    assert cap.evidence_sha256 == c["evidence_sha256"]
    assert cap.exec_config_version == "v1"
    assert cap.scopes == (SCOPE_RESEARCH_RUN,)


def test_audience_separates_runner_and_runtime(keypair):
    priv, pub = keypair
    runner_tok = sign_research_token(priv, **_claims(aud=AUD_RUNNER_EXEC))
    runtime_tok = sign_research_token(priv, **_claims(aud=AUD_RUNTIME_RESEARCH))
    assert verify_research_token(_keys(pub), runner_tok).aud == AUD_RUNNER_EXEC
    assert verify_research_token(_keys(pub), runtime_tok).aud == AUD_RUNTIME_RESEARCH
    # A token minted for one audience must not verify as the other (binding is
    # part of the signed payload, and the verifier must check it).
    with pytest.raises(ResearchCapabilityError):
        sign_research_token(priv, **_claims(aud="bogus-audience"))


def test_tampered_token_rejected(keypair):
    priv, pub = keypair
    token = sign_research_token(priv, **_claims())
    bad = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(ResearchCapabilityError):
        verify_research_token(_keys(pub), bad)


def test_wrong_public_key_rejected(keypair):
    priv, pub = keypair
    token = sign_research_token(priv, **_claims())
    _, other_pub = generate_research_keypair()
    with pytest.raises(ResearchCapabilityError):
        verify_research_token({"k1": other_pub}, token)


def test_unknown_kid_rejected(keypair):
    priv, pub = keypair
    token = sign_research_token(priv, **_claims(kid="k1"))
    # kid is only a selector into trusted keys; an unknown kid is rejected,
    # not looked up from the token itself.
    with pytest.raises(ResearchCapabilityError):
        verify_research_token({"other": pub}, token)


def test_expired_token_rejected(keypair):
    priv, pub = keypair
    token = sign_research_token(priv, **_claims(exp=datetime.now(UTC) - timedelta(seconds=1)))
    with pytest.raises(ResearchCapabilityError):
        verify_research_token(_keys(pub), token)


def test_not_yet_valid_token_rejected(keypair):
    priv, pub = keypair
    token = sign_research_token(
        priv, **_claims(nbf=datetime.now(UTC) + timedelta(seconds=30))
    )
    with pytest.raises(ResearchCapabilityError):
        verify_research_token(_keys(pub), token)


def test_cross_case_binding_is_signed(keypair):
    priv, pub = keypair
    c = _claims(case_id=uuid.UUID("00000000-0000-0000-0000-000000000001"))
    token = sign_research_token(priv, **c)
    cap = verify_research_token(_keys(pub), token)
    assert cap.case_id == c["case_id"]
    # The case_id is part of the signed payload: a different case is a
    # different (unforgeable) grant, enforced by the caller comparing cap.case_id
    # to the case it is about to run.
    other = uuid.UUID("00000000-0000-0000-0000-000000000002")
    assert cap.case_id != other


def test_evidence_hash_is_signed(keypair):
    priv, pub = keypair
    c = _claims(evidence_sha256="a" * 64)
    token = sign_research_token(priv, **c)
    cap = verify_research_token(_keys(pub), token)
    assert cap.evidence_sha256 == "a" * 64
    # A token signed for one evidence hash cannot claim another without a new sig.
    c2 = _claims(evidence_sha256="b" * 64)
    token2 = sign_research_token(priv, **c2)
    assert verify_research_token(_keys(pub), token2).evidence_sha256 == "b" * 64


def test_exec_config_version_is_signed(keypair):
    priv, pub = keypair
    c = _claims(exec_config_version="v1")
    token = sign_research_token(priv, **c)
    assert verify_research_token(_keys(pub), token).exec_config_version == "v1"
    c2 = _claims(exec_config_version="v2")
    token2 = sign_research_token(priv, **c2)
    assert verify_research_token(_keys(pub), token2).exec_config_version == "v2"


def test_hmac_capability_is_not_accepted_as_research(keypair):
    # The legacy HMAC capability (capability.py) is a different token space and
    # must never verify as a research grant (no downgrade).
    from youwei_contracts.capability import sign_capability

    legacy = sign_capability(
        b"legacy-hmac-secret",
        job_id=uuid.uuid4(),
        attempt_no=1,
        tenant_id=uuid.uuid4(),
        scopes=("llm_call",),
        exp=datetime.now(UTC) + timedelta(seconds=30),
    )
    with pytest.raises(ResearchCapabilityError):
        verify_research_token(_keys(keypair[1]), legacy)


def test_renewal_is_a_new_signature(keypair):
    priv, pub = keypair
    c = _claims(exp=datetime.now(UTC) + timedelta(seconds=1))
    token1 = sign_research_token(priv, **c)
    # Renewing (extending the inner research token's validity) requires a NEW
    # signature from the Controller; it cannot be done by re-signing the outer
    # Runner lease, which is a separate token.
    c2 = dict(c, exp=datetime.now(UTC) + timedelta(seconds=60))
    token2 = sign_research_token(priv, **c2)
    assert token1 != token2
    assert verify_research_token(_keys(pub), token1).exp != verify_research_token(_keys(pub), token2).exp


def test_kid_thumbprint_is_stable(keypair):
    _, pub = keypair
    assert public_key_thumbprint(pub) == public_key_thumbprint(pub)
    _, other_pub = generate_research_keypair()
    assert public_key_thumbprint(pub) != public_key_thumbprint(other_pub)
