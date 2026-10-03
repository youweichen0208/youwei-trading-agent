"""Settings parsing of the research signing key (S12d enablement).

The Ed25519 private key is stored in ``production.env`` as a single-line
PEM with literal ``\\n`` escapes (single-quoted so shell sourcing and
docker compose interpolation keep the backslashes). ``Settings`` must
restore the real newlines — ``load_pem_private_key`` receives the value
verbatim and a literal ``\\n`` PEM fails to parse. Discovered during the
2026-10-03 D1 deployment (docs/ops/s12d-deployment-execution.md)."""

from youwei_core.config import Settings


def _escaped_pem() -> str:
    lines = [
        "-----BEGIN PRIVATE KEY-----",
        "MC4CAQAwBQYDK2VwBCIEILastTurnModelAttributionPatchPlaceholder",
        "-----END PRIVATE KEY-----",
    ]
    return "\\n".join(lines) + "\\n"


def test_escaped_newlines_are_restored():
    s = Settings(research_signing_private_key=_escaped_pem())
    pem = s.research_signing_private_key
    assert "\\n" not in pem
    assert "\n" in pem
    assert pem.startswith("-----BEGIN PRIVATE KEY-----\n")
    assert pem.endswith("-----END PRIVATE KEY-----\n")


def test_real_newlines_pass_through_unchanged():
    raw = _escaped_pem().replace("\\n", "\n")
    s = Settings(research_signing_private_key=raw)
    assert s.research_signing_private_key == raw


def test_empty_stays_empty():
    assert Settings(research_signing_private_key="").research_signing_private_key == ""
