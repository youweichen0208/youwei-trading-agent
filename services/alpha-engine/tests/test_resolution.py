import pytest
from pydantic import ValidationError
from yta_alpha.fixtures import replay_observation
from yta_alpha.resolution import UnscorableEvidence


def test_delay_is_not_a_valid_unscorable_reason(calendar):
    observed = replay_observation(calendar)
    fields = dict(
        data_mode="REPLAY",
        target_session=observed.bars[0].session,
        reason="DATA_UNAVAILABLE",
        provider="synthetic-source",
        source_id="synthetic:delay",
        source_document_sha256="b" * 64,
        statement="Synthetic source has not yet published today's close.",
        published_at=observed.as_of,
        available_at=observed.as_of,
        retrieved_at=observed.as_of,
    )
    with pytest.raises(ValidationError):
        UnscorableEvidence(**fields)
    with pytest.raises(ValidationError):
        UnscorableEvidence(
            **{**fields, "reason": "TARGET_HALTED_NO_CLOSE", "source_document_sha256": "not-a-hash"}
        )
    with pytest.raises(ValidationError):
        UnscorableEvidence(**{**fields, "reason": "TARGET_HALTED_NO_CLOSE", "statement": ""})
